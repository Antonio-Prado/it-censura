#!/usr/bin/env python3
"""
Gestione utenti DNS Blacklist Manager.

Utilizzo:
  python manage_users.py list
  python manage_users.py add <utente>
  python manage_users.py passwd <utente>
  python manage_users.py email <utente> <email>
  python manage_users.py delete <utente>
  python manage_users.py enable <utente>
  python manage_users.py disable <utente>
"""
import getpass
import json
import os
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

USERS_FILE = Path(os.environ.get("USERS_FILE", Path(__file__).parent / "users.json"))


def _load() -> dict:
    if not USERS_FILE.exists():
        return {}
    with USERS_FILE.open() as f:
        return json.load(f)


def _save(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2) + "\n")
    USERS_FILE.chmod(0o600)


def _ask_password(username: str) -> str:
    while True:
        pw = getpass.getpass(f"Password per '{username}': ")
        if len(pw) < 8:
            print("La password deve essere di almeno 8 caratteri.", file=sys.stderr)
            continue
        pw2 = getpass.getpass("Conferma password: ")
        if pw != pw2:
            print("Le password non coincidono.", file=sys.stderr)
            continue
        return pw


def cmd_list(users: dict) -> None:
    if not users:
        print("Nessun utente configurato.")
        return
    print(f"{'Utente':<20} {'Stato':<14} {'Email'}")
    print("-" * 55)
    for name, data in sorted(users.items()):
        stato = "attivo" if data.get("active", True) else "disabilitato"
        email = data.get("email", "—")
        print(f"{name:<20} {stato:<14} {email}")


def cmd_add(users: dict, username: str) -> None:
    if username in users:
        print(f"Errore: l'utente '{username}' esiste già.", file=sys.stderr)
        sys.exit(1)
    pw = _ask_password(username)
    email = input("Email (lascia vuoto per saltare): ").strip().lower()
    entry: dict = {"password_hash": generate_password_hash(pw), "active": True}
    if email:
        entry["email"] = email
    users[username] = entry
    _save(users)
    print(f"Utente '{username}' creato.")


def cmd_passwd(users: dict, username: str) -> None:
    if username not in users:
        print(f"Errore: utente '{username}' non trovato.", file=sys.stderr)
        sys.exit(1)
    pw = _ask_password(username)
    users[username]["password_hash"] = generate_password_hash(pw)
    _save(users)
    print(f"Password di '{username}' aggiornata.")


def cmd_email(users: dict, username: str, email: str) -> None:
    if username not in users:
        print(f"Errore: utente '{username}' non trovato.", file=sys.stderr)
        sys.exit(1)
    users[username]["email"] = email.strip().lower()
    _save(users)
    print(f"Email di '{username}' impostata a '{email}'.")


def cmd_delete(users: dict, username: str) -> None:
    if username not in users:
        print(f"Errore: utente '{username}' non trovato.", file=sys.stderr)
        sys.exit(1)
    confirm = input(f"Eliminare definitivamente l'utente '{username}'? [s/N] ").strip().lower()
    if confirm != "s":
        print("Operazione annullata.")
        return
    del users[username]
    _save(users)
    print(f"Utente '{username}' eliminato.")


def cmd_enable(users: dict, username: str, active: bool) -> None:
    if username not in users:
        print(f"Errore: utente '{username}' non trovato.", file=sys.stderr)
        sys.exit(1)
    users[username]["active"] = active
    _save(users)
    stato = "abilitato" if active else "disabilitato"
    print(f"Utente '{username}' {stato}.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    users = _load()

    if cmd == "list":
        cmd_list(users)
    elif cmd == "add" and len(args) == 2:
        cmd_add(users, args[1])
    elif cmd == "passwd" and len(args) == 2:
        cmd_passwd(users, args[1])
    elif cmd == "email" and len(args) == 3:
        cmd_email(users, args[1], args[2])
    elif cmd == "delete" and len(args) == 2:
        cmd_delete(users, args[1])
    elif cmd == "enable" and len(args) == 2:
        cmd_enable(users, args[1], True)
    elif cmd == "disable" and len(args) == 2:
        cmd_enable(users, args[1], False)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
