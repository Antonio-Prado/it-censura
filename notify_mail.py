#!/usr/bin/env python3
"""Invia una mail di notifica: oggetto come argomento, testo da stdin.

Usato da weekly_update.sh per segnalare errori e avvisi dell'aggiornamento settimanale.
Configurazione con le stesse variabili d'ambiente dell'interfaccia web (app.py):
  ALERT_TO       destinatari, separati da virgola (obbligatorio)
  SMTP_HOST      server SMTP (relay)            SMTP_PORT  porta (default 587)
  SMTP_USER      utente (opzionale)              SMTP_PASSWORD
  SMTP_FROM      mittente (default SMTP_USER)    SMTP_TLS   true/false (default true)
Se il relay non e' configurato o rifiuta il messaggio (molti relay accettano solo il proprio
dominio), consegna direttamente al MX di ciascun destinatario, con STARTTLS: in questo caso
il mittente deve essere autorizzato dall'SPF del proprio dominio.

Uso: echo "testo" | notify_mail.py "Oggetto"    (--dry stampa senza inviare)
Exit: 0 inviato, 1 errore di invio, 2 configurazione mancante.
"""
import os
import smtplib
import subprocess
import sys
from email.message import EmailMessage


def env(key, default=""):
    return os.environ.get(key, default).strip()


def via_relay(msg):
    host = env("SMTP_HOST")
    if not host:
        raise OSError("SMTP_HOST non impostato")
    with smtplib.SMTP(host, int(env("SMTP_PORT", "587")), timeout=30) as s:
        if env("SMTP_TLS", "true").lower() in ("true", "1", "yes"):
            s.starttls()
        if env("SMTP_USER"):
            s.login(env("SMTP_USER"), os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def via_mx(msg, rcpt):
    domain = rcpt.rsplit("@", 1)[1]
    out = subprocess.run(["dig", "+short", "MX", domain],
                         capture_output=True, text=True).stdout.splitlines()
    hosts = [l.split()[1].rstrip(".") for l in sorted(
        (x for x in out if len(x.split()) == 2), key=lambda x: int(x.split()[0]))]
    last = None
    for h in hosts:
        try:
            with smtplib.SMTP(h, 25, timeout=30) as s:
                s.starttls()
                s.send_message(msg, to_addrs=[rcpt])
            return
        except Exception as e:  # noqa: BLE001 - prova il MX successivo
            last = e
    raise RuntimeError("consegna diretta a %s fallita: %s" % (domain, last))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    to = [a.strip() for a in env("ALERT_TO").split(",") if a.strip()]
    if not to:
        print("notify_mail: ALERT_TO non impostato", file=sys.stderr)
        return 2
    msg = EmailMessage()
    msg["Subject"] = args[0]
    msg["From"] = env("SMTP_FROM") or env("SMTP_USER") or "it-censura@localhost"
    msg["To"] = ", ".join(to)
    msg.set_content(sys.stdin.read())
    if "--dry" in sys.argv:
        print(msg)
        return 0
    try:
        via_relay(msg)
        return 0
    except (smtplib.SMTPException, OSError) as e:
        print("notify_mail: relay non disponibile (%s), consegna diretta" % e, file=sys.stderr)
    rc = 0
    for rcpt in to:
        try:
            via_mx(msg, rcpt)
        except Exception as e:  # noqa: BLE001
            print("notify_mail: %s" % e, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
