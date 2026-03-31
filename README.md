# DNS Blacklist Manager

Web GUI per la gestione delle blacklist DNS (Unbound + censorship) su FreeBSD.

## Requisiti

```sh
pkg install py311-flask
# oppure
pip install flask
```

## Installazione

1. Copia i file sul server nella directory desiderata (es. `/root/dns-gui/`):

```sh
scp -r app.py templates/ root@server:/root/dns-gui/
```

2. Assicurati che `censorship` sia installato in `/root/censorship/` (percorso di default).

## Avvio

```sh
# Avvio semplice (porta 5000)
python3 app.py

# Con porta e root censorship personalizzate
CENSORSHIP_ROOT=/root/censorship PORT=8080 python3 app.py
```

## Variabili d'ambiente

| Variabile          | Default              | Descrizione                          |
|--------------------|----------------------|--------------------------------------|
| `CENSORSHIP_ROOT`  | `/root/censorship`   | Directory installazione censorship   |
| `PORT`             | `5000`               | Porta HTTP della GUI                 |
| `UNBOUND_SERVICE`  | `unbound`            | Nome servizio rc FreeBSD             |

## Avvio automatico con rc.d (FreeBSD)

Crea `/usr/local/etc/rc.d/dns-gui`:

```sh
#!/bin/sh
# PROVIDE: dns_gui
# REQUIRE: NETWORKING unbound
# KEYWORD: shutdown

. /etc/rc.subr
name="dns_gui"
rcvar="dns_gui_enable"
command="/usr/local/bin/python3"
command_args="/root/dns-gui/app.py"
pidfile="/var/run/dns_gui.pid"
load_rc_config $name
run_rc_command "$1"
```

Poi:
```sh
chmod +x /usr/local/etc/rc.d/dns-gui
echo 'dns_gui_enable="YES"' >> /etc/rc.conf
service dns-gui start
```

## API

| Metodo   | Endpoint                   | Descrizione                                      |
|----------|----------------------------|--------------------------------------------------|
| `GET`    | `/api/search?domain=x.com` | Cerca un dominio in tutte le blacklist           |
| `GET`    | `/api/manual`              | Elenca tutti i domini nella blacklist manuale    |
| `POST`   | `/api/manual`              | Aggiunge `{"domain": "x.com"}` alla manuale     |
| `DELETE` | `/api/manual/<domain>`     | Rimuove un dominio dalla manuale                 |
| `GET`    | `/api/stats`               | Numero di voci per ogni blacklist                |
| `POST`   | `/api/reload`              | Ricarica il servizio Unbound                     |
