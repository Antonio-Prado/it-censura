# DNS Blacklist Manager

Web GUI for managing DNS blacklists on [Unbound](https://nlnetlabs.nl/projects/unbound/) — designed for ISPs and network operators that need to apply DNS-based filtering policies.

## Features

- **Domain search** — search across all blacklists in parallel (fast, grep-based)
- **Manual blacklist** — add/remove domains via the web UI
- **Whitelist** — domains excluded from blocking regardless of other lists
- **Statistics** — entry count per list, cached and refreshed in background
- **Blacklist update** — trigger an external update script and stream its output live
- **Unbound reload** — reload the DNS service after changes
- **User management** — session-based login, multiple users, hashed passwords
- **Audit log** — every write action and login/logout is logged

## Requirements

```sh
# FreeBSD
pkg install py311-flask py311-flask-login

# Linux / generic
pip install flask flask-login werkzeug
```

## Blacklist directory layout

The GUI auto-discovers all blacklist files in `BL_DIR`. Three file formats are supported:

| Extension | Format | Match pattern |
|-----------|--------|---------------|
| `.conf`   | Unbound `local-zone` | `local-zone: domain always_nxdomain` |
| `.txt`    | Plain domain list or hosts file | `domain` or `0.0.0.0 domain` |
| `.csv`    | Semicolon-separated | domain follows a `;` on each row |

Two special files are managed by the GUI:

- **`MANUAL_LIST`** — domains added manually via the UI (default: `BL_DIR/manual.txt`)
- **`WHITELIST`** — domains excluded from blocking (default: `BL_DIR/whitelist.txt`)

## Configuration (environment variables)

| Variable          | Default                          | Description                                     |
|-------------------|----------------------------------|-------------------------------------------------|
| `BL_DIR`          | `/etc/unbound/blacklists`        | Directory containing all blacklist files        |
| `MANUAL_LIST`     | `$BL_DIR/manual.txt`             | Path to the manually-managed blacklist          |
| `WHITELIST`       | `$BL_DIR/whitelist.txt`          | Path to the whitelist                           |
| `APPLY_CMD`       | *(empty)*                        | Shell command to run after manual/whitelist changes (e.g. reload Unbound config) |
| `UPDATE_CMD`      | *(empty)*                        | Shell command to run for a full blacklist update |
| `OFFICIAL_LISTS`  | `AAMS,ADMT,CNCPO,AGCOM,CONSOB,IVASS` | Comma-separated list stems shown with the "official" badge. Defaults to Italian regulatory lists (ADM gambling/tobacco, CNCPO, AGCOM, CONSOB, IVASS). Set to empty string to disable the badge. |
| `UNBOUND_SERVICE` | `unbound`                        | Service name for `service <name> reload`        |
| `UNBOUND_CONF_DIR`| `/usr/local/etc/unbound/blacklists.d` | Unbound include directory               |
| `PORT`            | `5000`                           | HTTP port                                       |
| `USERS_FILE`      | `<app_dir>/users.json`           | User credentials file                           |
| `AUDIT_LOG`       | `/var/log/dns_gui_audit.log`     | Audit log path                                  |
| `SECRET_KEY`      | *(auto-generated)*               | Flask session key                               |
| `SMTP_HOST`       | *(empty)*                        | SMTP server for password reset emails           |
| `SMTP_PORT`       | `587`                            |                                                 |
| `SMTP_USER`       | *(empty)*                        |                                                 |
| `SMTP_PASSWORD`   | *(empty)*                        |                                                 |
| `SMTP_FROM`       | `$SMTP_USER`                     |                                                 |
| `SMTP_TLS`        | `true`                           | Enable STARTTLS                                 |

## Quick start

```sh
# 1. Create the blacklist directory and add your lists
mkdir -p /etc/unbound/blacklists
# copy or symlink your .conf / .txt / .csv blacklist files here

# 2. Create the first user
python3 manage_users.py add admin

# 3. Start the GUI
BL_DIR=/etc/unbound/blacklists \
  APPLY_CMD="service unbound reload" \
  UPDATE_CMD="sh /opt/blacklists/update.sh" \
  python3 app.py
```

## Automatic startup (FreeBSD rc.d)

Copy `rc.d/dns-gui` to `/usr/local/etc/rc.d/dns-gui`, then:

```sh
chmod +x /usr/local/etc/rc.d/dns-gui
sysrc dns_gui_enable="YES"
sysrc dns_gui_bl_dir="/etc/unbound/blacklists"
sysrc dns_gui_apply_cmd="service unbound reload"
service dns-gui start
```

## User management

```sh
python3 manage_users.py add <username>      # add user (prompts for password)
python3 manage_users.py passwd <username>   # change password
python3 manage_users.py list                # list users
python3 manage_users.py disable <username>  # disable login
python3 manage_users.py delete <username>   # remove user
```

## API

| Method   | Endpoint                   | Description                                  |
|----------|----------------------------|----------------------------------------------|
| `GET`    | `/api/search?domain=x.com` | Search a domain across all blacklists        |
| `GET`    | `/api/manual`              | List all manually-blocked domains            |
| `POST`   | `/api/manual`              | Add `{"domain": "x.com"}` to manual list     |
| `DELETE` | `/api/manual/<domain>`     | Remove a domain from the manual list         |
| `GET`    | `/api/whitelist`           | List all whitelisted domains                 |
| `POST`   | `/api/whitelist`           | Add `{"domain": "x.com"}` to whitelist       |
| `DELETE` | `/api/whitelist/<domain>`  | Remove a domain from the whitelist           |
| `GET`    | `/api/stats`               | Entry count per blacklist                    |
| `POST`   | `/api/reload`              | Reload the Unbound service                   |
| `POST`   | `/api/update`              | Start a full blacklist update (streams output) |
| `GET`    | `/api/update/status`       | Poll update job status                       |
