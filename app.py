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
# Metadati delle voci manuali (commento, autore, data): JSON accanto alla lista manuale,
# che resta una semplice lista di domini leggibile da qualsiasi script.
MANUAL_META  = Path(os.environ.get("MANUAL_META", str(MANUAL_LIST.with_name(MANUAL_LIST.stem + "_meta.json"))))
WHITELIST    = Path(os.environ.get("WHITELIST",   str(BL_DIR / "whitelist.txt")))
# Storico dei conteggi per lista (per mostrare la variazione rispetto all'aggiornamento precedente)
STATS_HISTORY = Path(os.environ.get("STATS_HISTORY", str(BL_DIR / "stats_history.json")))
# Resolver da monitorare nell'intestazione: "etichetta=host,etichetta=host"
HEALTH_RESOLVERS = os.environ.get("HEALTH_RESOLVERS", "localhost=127.0.0.1")
HEALTH_PROBE_NAME = os.environ.get("HEALTH_PROBE_NAME", "example.com")
# Comandi shell opzionali (lasciare vuoto per disabilitare la funzione)
APPLY_CMD    = os.environ.get("APPLY_CMD", "")   # eseguito dopo modifiche alla lista manuale/whitelist
UPDATE_CMD   = os.environ.get("UPDATE_CMD", "")  # eseguito per l'aggiornamento completo delle blacklist

# ── Piracy Shield ─────────────────────────────────────────────────────────────
PS_SYNC_CMD    = os.environ.get("PS_SYNC_CMD", "")
PS_BGP_NEIGHBOR = os.environ.get("PS_BGP_NEIGHBOR", "")
PS_TOKEN_FILE  = Path(os.environ.get("PS_TOKEN_FILE",  Path.home() / ".ps_tokens.json"))
PS_STATE_FILE  = Path(os.environ.get("PS_STATE_FILE",  Path.home() / ".ps_state.json"))
PS_FQDN_CONF   = Path(os.environ.get("PS_FQDN_CONF",  "/usr/local/etc/unbound/blacklists.d/PS.conf"))
PS_IPV4_FILE   = Path(os.environ.get("PS_IPV4_FILE",  "/etc/unbound/blacklists/ps_ipv4.txt"))
PS_IPV6_FILE   = Path(os.environ.get("PS_IPV6_FILE",  "/etc/unbound/blacklists/ps_ipv6.txt"))

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
_stats_history_lock = threading.Lock()
_stats_prev: dict = {}          # {lista: {"count": conteggio precedente, "mtime": data precedente}}
_manual_lock = threading.Lock()

# ── Update job ────────────────────────────────────────────────────────────────
_update_job: dict = {"status": "idle", "started": None, "ended": None, "output": ""}
_update_lock = threading.Lock()

# ── Piracy Shield sync job ─────────────────────────────────────────────────────
_ps_job: dict = {"status": "idle", "started": None, "ended": None, "output": ""}
_ps_lock = threading.Lock()


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


MAX_COMMENT_LEN = 200


def _clean_comment(raw) -> str | None:
    """Normalizza un commento: spazi singoli, niente caratteri di controllo, max 200 caratteri.
    Ritorna None se non valido."""
    if raw is None:
        return ""
    text = " ".join(str(raw).split())
    if re.search(r"[\x00-\x1f\x7f]", text) or len(text) > MAX_COMMENT_LEN:
        return None
    return text


def _read_manual_meta() -> dict[str, dict]:
    """Metadati delle voci manuali {dominio: {comment, added_by, added_at}}. Chiamare dentro _manual_lock."""
    if not MANUAL_META.exists():
        return {}
    try:
        with MANUAL_META.open(errors="replace") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        app.logger.error("Lettura %s fallita: %s", MANUAL_META, exc)
        return {}


def _write_manual_meta(meta: dict[str, dict]) -> None:
    """Scrittura atomica (tmp + rename). Chiamare dentro _manual_lock."""
    tmp = MANUAL_META.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANUAL_META)


def _manual_entries() -> list[dict]:
    """Voci manuali ordinate con metadati. Chiamare dentro _manual_lock."""
    meta = _read_manual_meta()
    out = []
    for d in sorted(_read_manual()):
        m = meta.get(d, {})
        out.append({"domain": d, "comment": m.get("comment", ""),
                    "added_by": m.get("added_by", ""), "added_at": m.get("added_at", "")})
    return out


def _current_username() -> str:
    try:
        return current_user.id if current_user.is_authenticated else "anonymous"
    except Exception:
        return "anonymous"


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
    try:
        _update_stats_history(stats)
    except Exception as exc:
        app.logger.error("Aggiornamento storico statistiche fallito: %s", exc)
    _stats_cache = stats
    _stats_cache_ts = time.monotonic()
    _stats_building = False


def _iso_mtime(path: Path) -> str | None:
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()
        return ts.isoformat(timespec="seconds")
    except OSError:
        return None


def _blacklist_mtimes() -> dict[str, str | None]:
    """Data di ultima scrittura di ogni file lista (None se assente). Per i file scaricati con
    wget/curl che conservano il timestamp remoto, e' la data di pubblicazione della fonte."""
    return {name: _iso_mtime(path) for name, path in _get_blacklists().items()}


def _blacklist_synced() -> dict[str, str | None]:
    """Per le liste che hanno una configurazione Unbound generata in UNBOUND_CONF_DIR
    (<NOME>.conf, es. AGCOM.conf o MANUAL.conf): data dell'ultima elaborazione.
    Le liste gia' in formato .conf non compaiono (la data del file e' gia' quella)."""
    out: dict[str, str | None] = {}
    conf_dir = Path(UNBOUND_CONF_DIR)
    for name, path in _get_blacklists().items():
        if path.suffix == ".conf":
            continue
        base = name[4:] if name.startswith("raw_") else name
        for cand in (base, base.upper(), base.lower(), name):
            conf = conf_dir / f"{cand}.conf"
            if conf.exists():
                out[name] = _iso_mtime(conf)
                break
    return out


def _update_stats_history(stats: dict) -> None:
    """Storico conteggi: quando cambia l'mtime del file di una lista, il conteggio precedente
    viene conservato in prev_count. Popola _stats_prev per /api/stats."""
    global _stats_prev
    mtimes = _blacklist_mtimes()
    with _stats_history_lock:
        hist: dict = {}
        if STATS_HISTORY.exists():
            try:
                hist = json.loads(STATS_HISTORY.read_text())
                if not isinstance(hist, dict):
                    hist = {}
            except (OSError, ValueError):
                hist = {}
        for name, count in stats.items():
            if name.startswith("_"):
                continue
            mt = mtimes.get(name)
            h = hist.get(name)
            if not isinstance(h, dict):
                hist[name] = {"mtime": mt, "count": count, "prev_count": None, "prev_mtime": None}
            elif h.get("mtime") != mt:
                hist[name] = {"mtime": mt, "count": count,
                              "prev_count": h.get("count"), "prev_mtime": h.get("mtime")}
            else:
                h["count"] = count
        tmp = STATS_HISTORY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(hist, indent=1, sort_keys=True))
        os.replace(tmp, STATS_HISTORY)
        _stats_prev = {n: {"count": h.get("prev_count"), "mtime": h.get("prev_mtime")}
                       for n, h in hist.items() if h.get("prev_count") is not None}


# ── Stato dei resolver (query DNS reale via UDP, timeout breve) ───────────────
HEALTH_TIMEOUT = 2.0


def _dns_probe(host: str, timeout: float = HEALTH_TIMEOUT) -> dict:
    """Invia una query A per HEALTH_PROBE_NAME a host:53 e misura la latenza.
    Ritorna {"online": bool, "latency_ms": int|None, "error": str|None}."""
    import socket
    import struct
    qid = secrets.randbits(16)
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)   # flag RD
    qname = b"".join(bytes([len(l)]) + l.encode() for l in HEALTH_PROBE_NAME.split(".")) + b"\x00"
    query = header + qname + struct.pack("!HH", 1, 1)         # tipo A, classe IN
    start = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, 53, socket.AF_UNSPEC, socket.SOCK_DGRAM)
        if not infos:
            return {"online": False, "latency_ms": None, "error": "host non risolvibile"}
        family, socktype, proto, _, addr = infos[0]
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)
            sock.sendto(query, addr)
            while True:
                data, _ = sock.recvfrom(4096)
                if len(data) >= 12 and struct.unpack("!H", data[:2])[0] == qid:
                    break
        rcode = struct.unpack("!H", data[2:4])[0] & 0x000F
        ms = int((time.monotonic() - start) * 1000)
        if rcode == 2:   # SERVFAIL: risponde ma non risolve
            return {"online": True, "latency_ms": ms, "error": "SERVFAIL"}
        return {"online": True, "latency_ms": ms, "error": None}
    except socket.timeout:
        return {"online": False, "latency_ms": None, "error": "timeout"}
    except OSError as exc:
        return {"online": False, "latency_ms": None, "error": str(exc)}


def _health_targets() -> dict[str, str]:
    targets: dict[str, str] = {}
    for item in HEALTH_RESOLVERS.split(","):
        item = item.strip()
        if not item:
            continue
        label, _, host = item.partition("=")
        label, host = label.strip(), host.strip()
        if label and host:
            targets[label] = host
        elif label:
            targets[label] = label
    return targets


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
    manual_comment = ""
    if "manual" in found_in:
        with _manual_lock:
            manual_comment = _read_manual_meta().get(domain, {}).get("comment", "")
    return jsonify({
        "domain": domain,
        "found": bool(found_in),
        "lists": sorted(found_in),
        "in_manual": "manual" in found_in,
        "manual_comment": manual_comment,
        "in_whitelist": in_whitelist,
    })


@app.route("/api/manual", methods=["GET"])
@login_required
def api_manual_list():
    with _manual_lock:
        entries = _manual_entries()
    return jsonify({"domains": [e["domain"] for e in entries], "entries": entries})


def _manual_add_many(raw_domains: list, raw_comment) -> tuple[list[str], list[str], str | None]:
    """Aggiunge più domini alla lista manuale con lo stesso commento.
    Ritorna (aggiunti, errori, errore_fatale)."""
    comment = _clean_comment(raw_comment)
    if comment is None:
        return [], [], f"Commento non valido (max {MAX_COMMENT_LEN} caratteri, niente caratteri di controllo)"
    added: list[str] = []
    errors: list[str] = []
    with _manual_lock:
        existing_set = set(_read_manual())
        for raw in raw_domains:
            domain = _normalise(str(raw))
            if not domain:
                continue
            if not _domain_valid(domain):
                errors.append(f"'{domain}' non è un nome di dominio valido")
                continue
            if domain in existing_set:
                errors.append(f"'{domain}' è già nella blacklist manuale")
                continue
            existing_set.add(domain)
            added.append(domain)
        if added:
            MANUAL_LIST.parent.mkdir(parents=True, exist_ok=True)
            with MANUAL_LIST.open("a") as f:
                for d in added:
                    f.write(d + "\n")
            meta = _read_manual_meta()
            now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            who = _current_username()
            for d in added:
                meta[d] = {"comment": comment, "added_by": who, "added_at": now}
            _write_manual_meta(meta)
    return added, errors, None


@app.route("/api/manual", methods=["POST"])
@login_required
def api_manual_add():
    data = request.get_json(silent=True) or {}
    domain = _normalise(data.get("domain", ""))
    if not domain:
        return jsonify({"error": "Il dominio è obbligatorio"}), 400
    if not _domain_valid(domain):
        return jsonify({"error": f"'{domain}' non è un nome di dominio valido"}), 400

    added, errors, fatal = _manual_add_many([domain], data.get("comment", ""))
    if fatal:
        return jsonify({"error": fatal}), 400
    if not added:
        return jsonify({"error": errors[0] if errors else "Nessun dominio aggiunto"}), 409

    rc, out = _apply_manual()
    if rc != 0:
        return jsonify({"error": f"Dominio aggiunto ma rigenerazione fallita: {out}"}), 500

    comment = _clean_comment(data.get("comment", "")) or ""
    _audit("manual_add", f"{domain} | {comment or '-'}")
    return jsonify({"ok": True, "domain": domain, "comment": comment})


@app.route("/api/manual/bulk", methods=["POST"])
@login_required
def api_manual_bulk_add():
    """Aggiunge più domini in una sola operazione (una sola rigenerazione), con commento comune."""
    data = request.get_json(silent=True) or {}
    raw_domains = data.get("domains", [])
    if not isinstance(raw_domains, list) or not raw_domains:
        return jsonify({"error": "Nessun dominio fornito"}), 400

    added, errors, fatal = _manual_add_many(raw_domains, data.get("comment", ""))
    if fatal:
        return jsonify({"error": fatal}), 400

    if added:
        rc, out = _apply_manual()
        if rc != 0:
            return jsonify({"error": f"Domini aggiunti ma rigenerazione fallita: {out}",
                            "added": added, "errors": errors}), 500
        comment = _clean_comment(data.get("comment", "")) or "-"
        for d in added:
            _audit("manual_add", f"{d} | {comment}")

    return jsonify({"ok": True, "added": added, "errors": errors})


@app.route("/api/manual/<path:domain>", methods=["PATCH"])
@login_required
def api_manual_update_comment(domain):
    """Modifica il commento di una voce esistente (non tocca la lista né Unbound)."""
    domain = _normalise(domain)
    data = request.get_json(silent=True) or {}
    comment = _clean_comment(data.get("comment", ""))
    if comment is None:
        return jsonify({"error": f"Commento non valido (max {MAX_COMMENT_LEN} caratteri)"}), 400

    with _manual_lock:
        if domain not in _read_manual():
            return jsonify({"error": f"'{domain}' non è nella blacklist manuale"}), 404
        meta = _read_manual_meta()
        entry = meta.get(domain, {})
        entry["comment"] = comment
        entry.setdefault("added_by", "")
        entry.setdefault("added_at", "")
        meta[domain] = entry
        _write_manual_meta(meta)

    _audit("manual_comment", f"{domain} | {comment or '-'}")
    return jsonify({"ok": True, "domain": domain, "comment": comment})


@app.route("/api/manual/<path:domain>", methods=["DELETE"])
@login_required
def api_manual_delete(domain):
    domain = _normalise(domain)
    with _manual_lock:
        existing = _read_manual()
        if domain not in existing:
            return jsonify({"error": f"'{domain}' non è nella blacklist manuale"}), 404
        new_lines = [d for d in existing if d != domain]
        with MANUAL_LIST.open("w") as f:
            f.write("\n".join(new_lines) + ("\n" if new_lines else ""))
        meta = _read_manual_meta()
        if meta.pop(domain, None) is not None:
            _write_manual_meta(meta)

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
    return jsonify({**_stats_cache, "_loading": not cache_valid,
                    "_mtimes": _blacklist_mtimes(), "_synced": _blacklist_synced(),
                    "_prev": _stats_prev})


@app.route("/api/health")
@login_required
def api_health():
    """Stato dei resolver configurati in HEALTH_RESOLVERS: una query DNS reale a ciascuno."""
    targets = _health_targets()
    result: dict = {}
    if targets:
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
            futs = {ex.submit(_dns_probe, h): n for n, h in targets.items()}
            result = {futs[f]: f.result() for f in as_completed(futs)}
    result["_checked_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    result["_order"] = list(targets.keys())
    return jsonify(result)


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



# ── Piracy Shield helpers ──────────────────────────────────────────────────────

def _ps_count_conf() -> int:
    if not PS_FQDN_CONF.exists():
        return 0
    r = subprocess.run(["grep", "-c", "^local-zone:", str(PS_FQDN_CONF)],
                       capture_output=True, text=True, env=_env())
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _ps_count_ips(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


def _ps_token_info() -> dict:
    if not PS_TOKEN_FILE.exists():
        return {"refresh_expires_at": None}
    try:
        tokens = json.loads(PS_TOKEN_FILE.read_text())
        return {"refresh_expires_at": tokens.get("refresh_expires_at")}
    except Exception:
        return {"refresh_expires_at": None}


def _ps_bgp_state() -> str:
    if not PS_BGP_NEIGHBOR:
        return "non configurato"
    try:
        r = subprocess.run(
            ["bgpctl", "show", "neighbor", PS_BGP_NEIGHBOR],
            capture_output=True, text=True, timeout=5, env=_env(),
        )
        for line in (r.stdout + r.stderr).splitlines():
            if "BGP state" in line:
                return line.split("=", 1)[1].strip().split(",")[0].strip()
        return "sconosciuto"
    except Exception:
        return "errore"


def _run_ps_sync_bg():
    """Esegue PS_SYNC_CMD in background e registra l'output riga per riga."""
    global _ps_job
    if not PS_SYNC_CMD:
        with _ps_lock:
            _ps_job.update({"status": "error", "ended": time.strftime("%H:%M:%S"),
                            "output": "PS_SYNC_CMD non configurato."})
        return
    try:
        proc = subprocess.Popen(
            PS_SYNC_CMD, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=_env(),
        )
        lines = []
        for line in proc.stdout:
            lines.append(line.rstrip())
            with _ps_lock:
                _ps_job["output"] = "\n".join(lines)
        proc.wait()
        rc = proc.returncode
    except Exception as exc:
        rc = 1
        with _ps_lock:
            _ps_job["output"] += f"\nErrore: {exc}"

    with _ps_lock:
        _ps_job["status"] = "done" if rc == 0 else "error"
        _ps_job["ended"] = time.strftime("%H:%M:%S")

    global _stats_building
    _stats_building = True
    threading.Thread(target=_compute_stats_bg, daemon=True).start()


@app.route("/api/ps/status")
@login_required
def api_ps_status():
    token = _ps_token_info()
    now = time.time()
    exp = token["refresh_expires_at"]
    return jsonify({
        "enabled":            bool(PS_SYNC_CMD),
        "fqdn_count":         _ps_count_conf(),
        "ipv4_count":         _ps_count_ips(PS_IPV4_FILE),
        "ipv6_count":         _ps_count_ips(PS_IPV6_FILE),
        "refresh_expires_in": int(exp - now) if exp else None,
        "bgp_state":          _ps_bgp_state(),
    })


@app.route("/api/ps/sync", methods=["POST"])
@login_required
def api_ps_sync_start():
    with _ps_lock:
        if _ps_job["status"] == "running":
            return jsonify({"error": "Sincronizzazione PS già in corso"}), 409
        _ps_job.update({"status": "running", "started": time.strftime("%H:%M:%S"),
                        "ended": None, "output": ""})
    _audit("ps_sync_start")
    threading.Thread(target=_run_ps_sync_bg, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/ps/sync/status")
@login_required
def api_ps_sync_status():
    with _ps_lock:
        return jsonify(dict(_ps_job))


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
