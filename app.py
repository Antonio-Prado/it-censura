#!/usr/bin/env python3
"""
DNS Blacklist Manager - Interfaccia web per la gestione delle blacklist Unbound/censura
"""
from __future__ import annotations

import json
import os
import re
import secrets
import smtplib
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from flask import Flask, redirect, render_template, request, jsonify, url_for
from flask_login import (
    LoginManager, UserMixin,
    current_user, login_required, login_user, logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash

APP_DIR = Path(__file__).parent

# ── Secret key (persistente tra i restart) ────────────────────────────────────

def _get_secret_key() -> bytes:
    if "SECRET_KEY" in os.environ:
        return os.environ["SECRET_KEY"].encode()
    key_file = APP_DIR / "secret_key.bin"
    if key_file.exists():
        return key_file.read_bytes()
    key = secrets.token_bytes(32)
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key

app = Flask(__name__)
app.secret_key = _get_secret_key()

# ── flask-login ────────────────────────────────────────────────────────────────

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Accesso richiesto."

class User(UserMixin):
    def __init__(self, username: str):
        self.id = username

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    with USERS_FILE.open() as f:
        return json.load(f)

@login_manager.user_loader
def _user_loader(user_id: str):
    users = _load_users()
    u = users.get(user_id)
    if u and u.get("active", True):
        return User(user_id)
    return None

# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(action: str, detail: str = "-", username: str | None = None) -> None:
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if username is None:
        try:
            username = current_user.id if current_user.is_authenticated else "anonymous"
        except Exception:
            username = "anonymous"
    ip = request.remote_addr or "-"
    line = f"{ts} | {username} | {ip} | {action} | {detail}\n"
    try:
        with AUDIT_LOG.open("a") as f:
            f.write(line)
    except Exception as exc:
        app.logger.error("Audit log write failed: %s", exc)

# ── Password reset helpers ────────────────────────────────────────────────────

def _find_user_by_email(email: str) -> str | None:
    """Ritorna lo username associato all'email, o None."""
    email = email.strip().lower()
    for username, data in _load_users().items():
        if data.get("email", "").lower() == email:
            return username
    return None


def _create_reset_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    with _tokens_lock:
        # rimuovi token precedenti per lo stesso utente
        expired_or_same = [t for t, v in _reset_tokens.items()
                           if v["username"] == username or v["expires"] < datetime.now(timezone.utc)]
        for t in expired_or_same:
            _reset_tokens.pop(t, None)
        _reset_tokens[token] = {"username": username, "expires": expires}
    return token


def _consume_reset_token(token: str) -> str | None:
    """Valida e consuma il token; ritorna lo username o None se non valido."""
    with _tokens_lock:
        entry = _reset_tokens.get(token)
        if not entry:
            return None
        if entry["expires"] < datetime.now(timezone.utc):
            _reset_tokens.pop(token, None)
            return None
        _reset_tokens.pop(token)
        return entry["username"]


def _send_reset_email(to_email: str, token: str) -> None:
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST non configurato.")
    reset_url = request.host_url.rstrip("/") + url_for("reset_password", token=token)
    body = (
        f"Hai richiesto il reset della password per DNS Blacklist Manager.\n\n"
        f"Clicca il link seguente per impostare una nuova password (valido 1 ora):\n\n"
        f"  {reset_url}\n\n"
        f"Se non hai fatto questa richiesta, ignora questa email.\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Reset password — DNS Blacklist Manager"
    msg["From"]    = SMTP_FROM
    msg["To"]      = to_email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
        if SMTP_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


# ── Paths ─────────────────────────────────────────────────────────────────────
USERS_FILE   = Path(os.environ.get("USERS_FILE", APP_DIR / "users.json"))
AUDIT_LOG    = Path(os.environ.get("AUDIT_LOG", "/var/log/dns_gui_audit.log"))
BL_DIR       = Path(os.environ.get("BL_DIR", "/etc/unbound/blacklists"))
MANUAL_LIST  = Path(os.environ.get("MANUAL_LIST", str(BL_DIR / "manual.txt")))
WHITELIST    = Path(os.environ.get("WHITELIST",   str(BL_DIR / "whitelist.txt")))
# Comandi shell opzionali (lasciare vuoto per disabilitare la funzione)
APPLY_CMD    = os.environ.get("APPLY_CMD", "")   # eseguito dopo modifiche alla lista manuale/whitelist
UPDATE_CMD   = os.environ.get("UPDATE_CMD", "")  # eseguito per l'aggiornamento completo delle blacklist

# ── SMTP ──────────────────────────────────────────────────────────────────────
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM     = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_TLS      = os.environ.get("SMTP_TLS", "true").lower() not in ("false", "0", "no")

# ── Reset token store (in-memory, scadenza 1 ora) ─────────────────────────────
_reset_tokens: dict[str, dict] = {}   # token → {"username": str, "expires": datetime}
_tokens_lock  = threading.Lock()

UNBOUND_SERVICE  = os.environ.get("UNBOUND_SERVICE", "unbound")
UNBOUND_CONF_DIR = os.environ.get("UNBOUND_CONF_DIR", "/usr/local/etc/unbound/blacklists.d")

# Liste ufficiali/obbligatorie — mostrate con un badge distinto nell'interfaccia.
# Default: liste regolamentari italiane; sovrascrivibile con la variabile OFFICIAL_LISTS
# (nomi base separati da virgola corrispondenti ai file, es. "AGCOM,CNCPO,CONSOB").
_ITALIAN_OFFICIAL = "AAMS,ADMT,CNCPO,AGCOM,CONSOB,IVASS"
OFFICIAL_LISTS = sorted(
    s.strip() for s in
    os.environ.get("OFFICIAL_LISTS", _ITALIAN_OFFICIAL).split(",")
    if s.strip()
)


def _get_blacklists() -> dict[str, Path]:
    """Rileva automaticamente i file blacklist in BL_DIR a runtime.

    Formati supportati (rilevati dall'estensione):
      .conf  — formato Unbound local-zone  (local-zone: dominio always_nxdomain)
      .txt   — lista domini semplice o formato hosts  (0.0.0.0 dominio)
      .csv   — lista separata da punto e virgola dove il dominio segue un ';'
    I file corrispondenti a MANUAL_LIST o WHITELIST vengono esclusi automaticamente.
    """
    lists: dict[str, Path] = {}
    if BL_DIR.exists():
        try:
            manual_r = MANUAL_LIST.resolve()
            white_r  = WHITELIST.resolve()
        except Exception:
            manual_r = white_r = None
        for f in sorted(BL_DIR.iterdir()):
            if not f.is_file() or f.suffix not in (".conf", ".txt", ".csv"):
                continue
            try:
                if f.resolve() in (manual_r, white_r):
                    continue
            except Exception:
                pass
            lists[f.stem] = f
    lists["manual"] = MANUAL_LIST
    return lists

# ── Stats cache ────────────────────────────────────────────────────────────────
_stats_cache: dict = {}
_stats_cache_ts: float = 0.0
_stats_building = False
STATS_CACHE_TTL = 300  # secondi

# ── Update job ────────────────────────────────────────────────────────────────
_update_job: dict = {"status": "idle", "started": None, "ended": None, "output": ""}
_update_lock = threading.Lock()


# ── Utility ───────────────────────────────────────────────────────────────────

def _normalise(domain: str) -> str:
    return domain.strip().lower().rstrip(".")


def _domain_valid(domain: str) -> bool:
    return bool(re.match(r"^(?!\-)([a-z0-9\-]{1,63}\.)+[a-z]{2,}$", domain))


def _env() -> dict:
    e = os.environ.copy()
    e["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + e.get("PATH", "")
    return e


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env())
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def _read_manual() -> list[str]:
    """Legge la blacklist manuale (file piccolo, lettura diretta)."""
    if not MANUAL_LIST.exists():
        return []
    with MANUAL_LIST.open(errors="replace") as f:
        return [_normalise(ln) for ln in f if ln.strip() and not ln.startswith("#")]


def _read_whitelist() -> list[str]:
    """Legge la whitelist (file piccolo, lettura diretta)."""
    if not WHITELIST.exists():
        return []
    with WHITELIST.open(errors="replace") as f:
        return [_normalise(ln) for ln in f if ln.strip() and not ln.startswith("#")]


# ── grep-based search e conteggio ─────────────────────────────────────────────

def _grep_file(name: str, path: Path, domain: str) -> str | None:
    """Cerca il dominio in un file usando grep -F (stringa fissa, velocissimo)."""
    if not path.exists():
        return None
    if path.suffix == ".conf":
        # BL_*.conf: "local-zone: domain always_nxdomain" (senza virgolette)
        # Lo spazio finale evita falsi positivi con sottodomini
        cmd = ["grep", "-qF", f"local-zone: {domain} ", str(path)]
    elif path.suffix == ".csv":
        # CNCPO: dominio nella colonna dopo il separatore
        cmd = ["grep", "-qF", f";{domain}", str(path)]
    else:
        # formato hosts "0.0.0.0 domain" oppure plain "domain"
        # cerca " domain" (con spazio) o "domain" a inizio riga
        r1 = subprocess.run(["grep", "-qF", f" {domain}", str(path)],
                            capture_output=True, env=_env())
        if r1.returncode == 0:
            return name
        r2 = subprocess.run(["grep", "-qxF", domain, str(path)],
                            capture_output=True, env=_env())
        return name if r2.returncode == 0 else None
    r = subprocess.run(cmd, capture_output=True, env=_env())
    return name if r.returncode == 0 else None


def _count_file(path: Path) -> int:
    """Conta le voci di una lista usando grep -c (velocissimo)."""
    if not path.exists():
        return 0
    if path.suffix == ".conf":
        cmd = ["grep", "-c", "^local-zone:", str(path)]
    elif path.suffix == ".csv":
        r = subprocess.run(["grep", "-c", ";", str(path)],
                           capture_output=True, text=True, env=_env())
        try:
            return max(0, int(r.stdout.strip()) - 1)  # -1 per riga header
        except Exception:
            return 0
    else:
        cmd = ["grep", "-cvE", r"^[[:space:]]*(#|$)", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _compute_stats_bg():
    """Calcola statistiche in parallelo con grep, senza toccare il GIL."""
    global _stats_cache, _stats_cache_ts, _stats_building
    stats = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_count_file, path): name for name, path in _get_blacklists().items()}
        for fut in as_completed(futs):
            stats[futs[fut]] = fut.result()
    _stats_cache = stats
    _stats_cache_ts = time.monotonic()
    _stats_building = False


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = _load_users()
        u = users.get(username)
        if u and u.get("active", True) and check_password_hash(u["password_hash"], password):
            login_user(User(username), remember=False)
            _audit("login", username=username)
            return redirect(url_for("index"))
        _audit("login_failed", detail=f"username={username!r}", username=username)
        error = "Credenziali non valide."
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    _audit("logout")
    logout_user()
    return redirect(url_for("login"))


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    message = None
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = _find_user_by_email(email)
        if username:
            token = _create_reset_token(username)
            try:
                _send_reset_email(email, token)
            except Exception as exc:
                app.logger.error("Send reset email failed: %s", exc)
                error = "Errore nell'invio dell'email. Contatta l'amministratore."
            else:
                _audit("password_reset_requested", detail=email, username=username)
        # messaggio generico per non rivelare quali email sono registrate
        if not error:
            message = "Se l'indirizzo è registrato, riceverai un'email con le istruzioni."
    return render_template("forgot.html", message=message, error=error)


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    # verifica token senza consumarlo (GET)
    with _tokens_lock:
        entry = _reset_tokens.get(token)
        token_valid = entry is not None and entry["expires"] >= datetime.now(timezone.utc)
    if not token_valid:
        return render_template("reset.html", token=token, expired=True, error=None)
    if request.method == "POST":
        pw  = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if len(pw) < 8:
            error = "La password deve essere di almeno 8 caratteri."
        elif pw != pw2:
            error = "Le password non coincidono."
        else:
            username = _consume_reset_token(token)
            if not username:
                return render_template("reset.html", token=token, expired=True, error=None)
            users = _load_users()
            users[username]["password_hash"] = generate_password_hash(pw)
            USERS_FILE.write_text(json.dumps(users, indent=2) + "\n")
            _audit("password_reset", username=username)
            return render_template("reset.html", token=token, expired=False, done=True, error=None)
    return render_template("reset.html", token=token, expired=False, done=False, error=error)


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", official_lists=OFFICIAL_LISTS)


@app.route("/help")
@login_required
def help_page():
    return render_template("help.html")


@app.route("/api/search")
@login_required
def api_search():
    domain = _normalise(request.args.get("domain", ""))
    if not domain:
        return jsonify({"error": "Il dominio è obbligatorio"}), 400
    if not _domain_valid(domain):
        return jsonify({"error": f"'{domain}' non è un nome di dominio valido"}), 400

    found_in = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_grep_file, n, p, domain): n for n, p in _get_blacklists().items()}
        for fut in as_completed(futs):
            result = fut.result()
            if result:
                found_in.append(result)

    in_whitelist = domain in _read_whitelist()
    return jsonify({
        "domain": domain,
        "found": bool(found_in),
        "lists": sorted(found_in),
        "in_manual": "manual" in found_in,
        "in_whitelist": in_whitelist,
    })


@app.route("/api/manual", methods=["GET"])
@login_required
def api_manual_list():
    return jsonify({"domains": sorted(_read_manual())})


@app.route("/api/manual", methods=["POST"])
@login_required
def api_manual_add():
    data = request.get_json(silent=True) or {}
    domain = _normalise(data.get("domain", ""))
    if not domain:
        return jsonify({"error": "Il dominio è obbligatorio"}), 400
    if not _domain_valid(domain):
        return jsonify({"error": f"'{domain}' non è un nome di dominio valido"}), 400

    existing = _read_manual()
    if domain in existing:
        return jsonify({"error": f"'{domain}' è già nella blacklist manuale"}), 409

    with MANUAL_LIST.open("a") as f:
        f.write(domain + "\n")

    rc, out = _apply_manual()
    if rc != 0:
        return jsonify({"error": f"Dominio aggiunto ma rigenerazione fallita: {out}"}), 500

    _audit("manual_add", domain)
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/manual/<path:domain>", methods=["DELETE"])
@login_required
def api_manual_delete(domain):
    domain = _normalise(domain)
    existing = _read_manual()
    if domain not in existing:
        return jsonify({"error": f"'{domain}' non è nella blacklist manuale"}), 404

    new_lines = [d for d in existing if d != domain]
    with MANUAL_LIST.open("w") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))

    rc, out = _apply_manual()
    if rc != 0:
        return jsonify({"error": f"Dominio rimosso ma rigenerazione fallita: {out}"}), 500

    _audit("manual_delete", domain)
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/whitelist", methods=["GET"])
@login_required
def api_whitelist_list():
    return jsonify({"domains": sorted(_read_whitelist())})


@app.route("/api/whitelist", methods=["POST"])
@login_required
def api_whitelist_add():
    data = request.get_json(silent=True) or {}
    domain = _normalise(data.get("domain", ""))
    if not domain:
        return jsonify({"error": "Il dominio è obbligatorio"}), 400
    if not _domain_valid(domain):
        return jsonify({"error": f"'{domain}' non è un nome di dominio valido"}), 400

    existing = _read_whitelist()
    if domain in existing:
        return jsonify({"error": f"'{domain}' è già nella whitelist"}), 409

    with WHITELIST.open("a") as f:
        f.write(domain + "\n")

    rc, out = _apply_whitelist()
    if rc != 0:
        return jsonify({"error": f"Dominio aggiunto ma rigenerazione fallita: {out}"}), 500

    _audit("whitelist_add", domain)
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/whitelist/<path:domain>", methods=["DELETE"])
@login_required
def api_whitelist_delete(domain):
    domain = _normalise(domain)
    existing = _read_whitelist()
    if domain not in existing:
        return jsonify({"error": f"'{domain}' non è nella whitelist"}), 404

    new_lines = [d for d in existing if d != domain]
    with WHITELIST.open("w") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))

    rc, out = _apply_whitelist()
    if rc != 0:
        return jsonify({"error": f"Dominio rimosso ma rigenerazione fallita: {out}"}), 500

    _audit("whitelist_delete", domain)
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/reload", methods=["POST"])
@login_required
def api_reload():
    rc, out = _run(["service", UNBOUND_SERVICE, "reload"])
    if rc != 0:
        return jsonify({"error": out}), 500
    _audit("unbound_reload")
    return jsonify({"ok": True, "output": out})


@app.route("/api/stats")
@login_required
def api_stats():
    global _stats_building
    now = time.monotonic()
    cache_valid = bool(_stats_cache) and (now - _stats_cache_ts) < STATS_CACHE_TTL
    if not cache_valid and not _stats_building:
        _stats_building = True
        threading.Thread(target=_compute_stats_bg, daemon=True).start()
    return jsonify({**_stats_cache, "_loading": not cache_valid})


def _run_update_bg():
    """Esegue UPDATE_CMD in background e registra l'output riga per riga."""
    global _update_job
    if not UPDATE_CMD:
        with _update_lock:
            _update_job.update({"status": "error", "ended": time.strftime("%H:%M:%S"),
                                "output": "UPDATE_CMD non configurato."})
        return
    env = _env()
    try:
        proc = subprocess.Popen(
            UPDATE_CMD, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env
        )
        lines = []
        for line in proc.stdout:
            lines.append(line.rstrip())
            with _update_lock:
                _update_job["output"] = "\n".join(lines)
        proc.wait()
        rc = proc.returncode
    except Exception as exc:
        rc = 1
        with _update_lock:
            _update_job["output"] += f"\nErrore: {exc}"

    with _update_lock:
        _update_job["status"] = "done" if rc == 0 else "error"
        _update_job["ended"] = time.strftime("%H:%M:%S")

    # Invalida la cache delle statistiche
    global _stats_building
    _stats_building = True
    threading.Thread(target=_compute_stats_bg, daemon=True).start()


@app.route("/api/update", methods=["POST"])
@login_required
def api_update_start():
    with _update_lock:
        if _update_job["status"] == "running":
            return jsonify({"error": "Aggiornamento già in corso"}), 409
        _update_job.update({"status": "running", "started": time.strftime("%H:%M:%S"),
                            "ended": None, "output": ""})
    _audit("update_start")
    threading.Thread(target=_run_update_bg, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/update/status")
@login_required
def api_update_status():
    with _update_lock:
        return jsonify(dict(_update_job))



def _apply_changes() -> tuple[int, str]:
    """Esegue APPLY_CMD dopo una modifica alla lista manuale o whitelist. Nessuna operazione se non configurato."""
    if not APPLY_CMD:
        return 0, ""
    try:
        r = subprocess.run(
            APPLY_CMD, shell=True, capture_output=True, text=True,
            timeout=300, env=_env()
        )
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            app.logger.error("APPLY_CMD failed (rc=%d): %s", r.returncode, out)
        return r.returncode, out
    except Exception as exc:
        app.logger.error("APPLY_CMD exception: %s", exc)
        return 1, str(exc)


def _apply_whitelist() -> tuple[int, str]:
    return _apply_changes()


def _apply_manual() -> tuple[int, str]:
    return _apply_changes()


if __name__ == "__main__":
    # Avvia il calcolo delle statistiche subito in background
    _stats_building = True
    threading.Thread(target=_compute_stats_bg, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=False, threaded=True)
