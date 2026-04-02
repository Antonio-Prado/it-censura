#!/usr/bin/env python3
"""
Sincronizza la blacklist Piracy Shield (AGCOM) con Unbound e il BGP blackhole.

Ad ogni esecuzione lo script:
  1. Autentica con l'API Piracy Shield (JWT con refresh automatico).
  2. Scarica la lista completa degli FQDN → scrive PS.conf in formato Unbound.
  3. Scarica la lista completa degli IPv4 e IPv6 → scrive il file IP per il BGP blackhole.
  4. Per ogni ticket degli ultimi 48 ore imposta lo stato di ciascun item:
       - "processed"       se l'item è nuovo (mai visto prima)
       - "ALREADY_BLOCKED" se l'item era già presente in un ticket precedente
  5. Aggiorna il file di stato locale con tutti gli item correnti.

Variabili d'ambiente:
  PS_URL           URL base dell'API (es. https://psp01.agcom.it/api)  [obbligatoria]
  PS_EMAIL         Email di accesso                                     [obbligatoria]
  PS_PASSWORD      Password di accesso                                  [obbligatoria]
  PS_TOKEN_FILE    File JSON dove salvare i token JWT
                   (default: ~/.ps_tokens.json)
  PS_STATE_FILE    File JSON dove salvare gli item già processati
                   (default: ~/.ps_state.json)
  PS_FQDN_CONF     File .conf Unbound di output per i domini
                   (default: /usr/local/etc/unbound/blacklists.d/PS.conf)
  PS_IPV4_FILE     File di output IPv4 per BGP blackhole
                   (default: /etc/unbound/blacklists/ps_ipv4.txt)
  PS_IPV6_FILE     File di output IPv6 per BGP blackhole
                   (default: /etc/unbound/blacklists/ps_ipv6.txt)
  PS_MARK_PROCESSED  Se impostare lo stato degli item sull'API (default: true)
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# ── Configurazione ─────────────────────────────────────────────────────────────

PS_URL         = os.environ.get("PS_URL", "").rstrip("/")
PS_EMAIL       = os.environ.get("PS_EMAIL", "")
PS_PASSWORD    = os.environ.get("PS_PASSWORD", "")
PS_TOKEN_FILE  = Path(os.environ.get("PS_TOKEN_FILE",  Path.home() / ".ps_tokens.json"))
PS_STATE_FILE  = Path(os.environ.get("PS_STATE_FILE",  Path.home() / ".ps_state.json"))
PS_FQDN_CONF   = Path(os.environ.get("PS_FQDN_CONF",  "/usr/local/etc/unbound/blacklists.d/PS.conf"))
PS_IPV4_FILE   = Path(os.environ.get("PS_IPV4_FILE",  "/etc/unbound/blacklists/ps_ipv4.txt"))
PS_IPV6_FILE   = Path(os.environ.get("PS_IPV6_FILE",  "/etc/unbound/blacklists/ps_ipv6.txt"))
PS_MARK_PROCESSED = os.environ.get("PS_MARK_PROCESSED", "true").lower() not in ("false", "0", "no")

TIMEOUT = 30


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


# ── Gestione token ─────────────────────────────────────────────────────────────

def _load_tokens() -> dict:
    if PS_TOKEN_FILE.exists():
        try:
            return json.loads(PS_TOKEN_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_tokens(tokens: dict) -> None:
    PS_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PS_TOKEN_FILE.write_text(json.dumps(tokens, indent=2) + "\n")
    PS_TOKEN_FILE.chmod(0o600)


def _login() -> dict:
    _log("Login con credenziali...")
    resp = requests.post(
        f"{PS_URL}/v1/authentication/login",
        json={"email": PS_EMAIL, "password": PS_PASSWORD},
        verify=False, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    now = time.time()
    tokens = {
        "access_token":        data["access_token"],
        "refresh_token":       data["refresh_token"],
        "access_expires_at":   now + 3500,   # 1h con 100s di margine
        "refresh_expires_at":  now + 7 * 86400 - 300,
    }
    _save_tokens(tokens)
    _log("Login effettuato.")
    return tokens


def _refresh(tokens: dict) -> dict:
    _log("Rinnovo access token...")
    resp = requests.post(
        f"{PS_URL}/v1/authentication/refresh",
        json={"refresh_token": tokens["refresh_token"]},
        verify=False, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    tokens["access_token"]      = resp.json()["data"]["access_token"]
    tokens["access_expires_at"] = time.time() + 3500
    _save_tokens(tokens)
    _log("Access token rinnovato.")
    return tokens


def get_token() -> str:
    """Restituisce un access token valido, effettuando refresh o login se necessario."""
    tokens = _load_tokens()
    now = time.time()

    if not tokens:
        tokens = _login()
        return tokens["access_token"]

    if now >= tokens.get("refresh_expires_at", 0):
        tokens = _login()
        return tokens["access_token"]

    if now >= tokens.get("access_expires_at", 0):
        tokens = _refresh(tokens)

    return tokens["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


# ── Pre-flight check ───────────────────────────────────────────────────────────

def _check_connectivity() -> None:
    """Verifica che la piattaforma sia raggiungibile prima di procedere.

    Un fallimento di connessione indica quasi sempre che la VPN non è attiva.
    """
    try:
        requests.get(f"{PS_URL}/v1/ping", verify=False, timeout=10)
    except requests.exceptions.ConnectionError:
        _log(
            "Errore: impossibile raggiungere la piattaforma Piracy Shield.\n"
            "  Verificare che la VPN site-to-site verso Azure sia attiva.\n"
            f"  Endpoint: {PS_URL}\n"
            "  Diagnosi: sh vpn/ps_vpn_check.sh"
        )
        sys.exit(1)
    except requests.exceptions.Timeout:
        _log(
            "Errore: timeout nella connessione alla piattaforma Piracy Shield.\n"
            "  La VPN è attiva ma la piattaforma non risponde."
        )
        sys.exit(1)


# ── Gestione stato locale ──────────────────────────────────────────────────────

def _load_state() -> dict:
    """Carica il file di stato con i set degli item già processati."""
    if PS_STATE_FILE.exists():
        try:
            raw = json.loads(PS_STATE_FILE.read_text())
            return {
                "fqdns": set(raw.get("fqdns", [])),
                "ips":   set(raw.get("ips",   [])),
            }
        except Exception:
            pass
    return {"fqdns": set(), "ips": set()}


def _save_state(state: dict) -> None:
    PS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PS_STATE_FILE.write_text(json.dumps({
        "fqdns": sorted(state["fqdns"]),
        "ips":   sorted(state["ips"]),
    }, indent=2) + "\n")
    PS_STATE_FILE.chmod(0o600)


# ── Chiamate API ───────────────────────────────────────────────────────────────

def _get_txt(path: str) -> list[str]:
    """Scarica un endpoint /txt e restituisce le righe non vuote."""
    resp = requests.get(
        f"{PS_URL}{path}",
        headers=_headers(), verify=False, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return [ln.strip() for ln in resp.text.splitlines() if ln.strip()]


def _get_all_tickets() -> list:
    resp = requests.get(
        f"{PS_URL}/v1/ticket/get/all",
        headers=_headers(), verify=False, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _set_processed(value: str) -> bool:
    try:
        resp = requests.post(
            f"{PS_URL}/v1/ticket/item/set/processed",
            headers=_headers(),
            json={"value": value},
            verify=False, timeout=TIMEOUT,
        )
        return resp.status_code == 200
    except Exception as e:
        _log(f"Attenzione: set_processed({value!r}) fallito: {e}")
        return False


def _set_already_blocked(value: str) -> bool:
    try:
        resp = requests.post(
            f"{PS_URL}/v1/ticket/item/set/unprocessed",
            headers=_headers(),
            json={"value": value, "reason": "ALREADY_BLOCKED"},
            verify=False, timeout=TIMEOUT,
        )
        return resp.status_code == 200
    except Exception as e:
        _log(f"Attenzione: set_already_blocked({value!r}) fallito: {e}")
        return False


# ── Output ─────────────────────────────────────────────────────────────────────

def _write_unbound(fqdns: list[str]) -> None:
    PS_FQDN_CONF.parent.mkdir(parents=True, exist_ok=True)
    with PS_FQDN_CONF.open("w") as f:
        f.write("server:\n")
        for d in sorted(set(fqdns)):
            f.write(f'local-zone: "{d}" always_nxdomain\n')
    _log(f"PS.conf: {len(fqdns)} domini → {PS_FQDN_CONF}")


def _write_ip_file(path: Path, ips: list[str], label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ip in sorted(set(ips)):
            f.write(ip + "\n")
    _log(f"{label}: {len(ips)} indirizzi → {path}")


# ── Logica principale ──────────────────────────────────────────────────────────

def _is_within_48h(created_at: str) -> bool:
    """Verifica se il ticket è stato creato nelle ultime 48 ore (finestra per set_processed)."""
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt < timedelta(hours=48)
    except Exception:
        return False


def main() -> None:
    # Verifica variabili obbligatorie
    missing = [v for v in ("PS_URL", "PS_EMAIL", "PS_PASSWORD") if not os.environ.get(v)]
    if missing:
        _log(f"Errore: variabili d'ambiente mancanti: {', '.join(missing)}")
        sys.exit(1)

    _check_connectivity()
    state = _load_state()
    errors = 0

    # 1. Scarica le liste complete
    _log("Scarico lista FQDN...")
    try:
        fqdns = _get_txt("/v1/fqdn/get/all/txt")
    except Exception as e:
        _log(f"Errore nel recupero FQDN: {e}")
        sys.exit(1)

    _log("Scarico lista IPv4...")
    try:
        ipv4s = _get_txt("/v1/ipv4/get/all/txt")
    except Exception as e:
        _log(f"Errore nel recupero IPv4: {e}")
        sys.exit(1)

    _log("Scarico lista IPv6...")
    try:
        ipv6s = _get_txt("/v1/ipv6/get/all/txt")
    except Exception as e:
        _log(f"Errore nel recupero IPv6: {e}")
        sys.exit(1)

    # 2. Scrivi i file di output
    _write_unbound(fqdns)
    _write_ip_file(PS_IPV4_FILE, ipv4s, "IPv4")
    _write_ip_file(PS_IPV6_FILE, ipv6s, "IPv6")

    # 3. Imposta lo stato degli item per i ticket recenti (entro 48h)
    if PS_MARK_PROCESSED:
        _log("Recupero ticket per aggiornamento stato...")
        try:
            tickets = _get_all_tickets()
        except Exception as e:
            _log(f"Attenzione: impossibile recuperare i ticket: {e}")
            tickets = []

        for ticket in tickets:
            ticket_id  = ticket.get("ticket_id", "?")
            created_at = ticket.get("metadata", {}).get("created_at", "")
            if not _is_within_48h(created_at):
                continue

            all_items = (
                [(f, "fqdn") for f in ticket.get("fqdn",  [])] +
                [(i, "ip")   for i in ticket.get("ipv4",  [])] +
                [(i, "ip")   for i in ticket.get("ipv6",  [])]
            )
            for value, kind in all_items:
                bucket = "fqdns" if kind == "fqdn" else "ips"
                if value in state[bucket]:
                    ok = _set_already_blocked(value)
                    _log(f"  ticket {ticket_id}: {value} → ALREADY_BLOCKED ({'ok' if ok else 'ERRORE'})")
                    if not ok:
                        errors += 1
                else:
                    ok = _set_processed(value)
                    _log(f"  ticket {ticket_id}: {value} → processed ({'ok' if ok else 'ERRORE'})")
                    if not ok:
                        errors += 1

    # 4. Aggiorna lo stato locale con tutti gli item correnti
    state["fqdns"].update(fqdns)
    state["ips"].update(ipv4s)
    state["ips"].update(ipv6s)
    _save_state(state)
    _log(f"Stato locale aggiornato: {len(state['fqdns'])} FQDN, {len(state['ips'])} IP.")

    if errors:
        _log(f"Completato con {errors} errori nell'aggiornamento stato.")
        sys.exit(1)
    _log("Sincronizzazione completata.")


if __name__ == "__main__":
    main()
