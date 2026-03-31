#!/bin/sh
# update.sh — Download and convert all official Italian DNS blacklists.
#
# Each list is downloaded (where applicable) and converted to Unbound
# local-zone format via parse.py.  Failures on individual lists are logged
# but do not abort the whole run.
#
# Configuration via environment variables:
#
#   BL_DIR      Working directory for raw downloads  (default: /etc/unbound/blacklists)
#   CONF_DIR    Unbound conf include directory       (default: /usr/local/etc/unbound/blacklists.d)
#   WHITELIST   Whitelist file                       (default: $BL_DIR/whitelist.txt)
#
#   CNCPO_FILE  Path to the CNCPO CSV file.
#               The CNCPO list is NOT publicly downloadable; ISPs receive it
#               through a dedicated channel.  Set this to its local path.
#
#   AAMS_URL    URL of the ADM gambling blacklist (plain-text domain list).
#   ADMT_URL    URL of the ADM tobacco blacklist  (plain-text domain list).
#               These are published by Agenzia delle Dogane e dei Monopoli.
#               Configure the current URLs from the official ADM portal.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
CONF_DIR="${CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
WHITELIST="${WHITELIST:-${BL_DIR}/whitelist.txt}"

mkdir -p "${BL_DIR}" "${CONF_DIR}"

log()  { printf '[%s] %s\n'         "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

errors=0

# Run parse.py on an input file and write <name>.conf to CONF_DIR.
parse() {
    fmt="$1"; infile="$2"; name="$3"
    wl_opt=
    [ -f "${WHITELIST}" ] && wl_opt="-w ${WHITELIST}"
    # shellcheck disable=SC2086
    python3 "${SCRIPT_DIR}/parse.py" \
        -f "${fmt}" -i "${infile}" -o "${CONF_DIR}/${name}.conf" ${wl_opt}
}

# ── CNCPO — child protection (not publicly downloadable) ──────────────────────
if [ -n "${CNCPO_FILE}" ] && [ -f "${CNCPO_FILE}" ]; then
    log "--- CNCPO ---"
    parse cncpo "${CNCPO_FILE}" CNCPO \
        || { warn "CNCPO failed"; errors=$((errors + 1)); }
else
    log "CNCPO: CNCPO_FILE not set or not found — skipping."
fi

# ── AAMS — ADM gambling blacklist ─────────────────────────────────────────────
if [ -n "${AAMS_URL}" ]; then
    log "--- AAMS ---"
    RAW="${BL_DIR}/raw_aams.txt"
    curl -fsSL --max-time 60 "${AAMS_URL}" -o "${RAW}" \
        && parse plain "${RAW}" AAMS \
        || { warn "AAMS failed"; errors=$((errors + 1)); }
else
    log "AAMS: AAMS_URL not set — skipping."
fi

# ── ADMT — ADM tobacco blacklist ──────────────────────────────────────────────
if [ -n "${ADMT_URL}" ]; then
    log "--- ADMT ---"
    RAW="${BL_DIR}/raw_admt.txt"
    curl -fsSL --max-time 60 "${ADMT_URL}" -o "${RAW}" \
        && parse plain "${RAW}" ADMT \
        || { warn "ADMT failed"; errors=$((errors + 1)); }
else
    log "ADMT: ADMT_URL not set — skipping."
fi

# ── AGCOM — copyright protection ──────────────────────────────────────────────
log "--- AGCOM ---"
RAW="${BL_DIR}/raw_agcom.bin"
python3 "${SCRIPT_DIR}/download_agcom.py" -o "${RAW}" \
    && parse plain "${RAW}" AGCOM \
    || { warn "AGCOM failed"; errors=$((errors + 1)); }

# ── CONSOB — financial fraud ──────────────────────────────────────────────────
log "--- CONSOB ---"
RAW="${BL_DIR}/raw_consob.txt"
python3 "${SCRIPT_DIR}/download_consob.py" -o "${RAW}" \
    && parse plain "${RAW}" CONSOB \
    || { warn "CONSOB failed"; errors=$((errors + 1)); }

# ── IVASS — insurance/broker fraud ────────────────────────────────────────────
log "--- IVASS ---"
RAW="${BL_DIR}/raw_ivass.txt"
python3 "${SCRIPT_DIR}/download_ivass.py" -o "${RAW}" \
    && parse plain "${RAW}" IVASS \
    || { warn "IVASS failed"; errors=$((errors + 1)); }

log "=== Update complete — ${errors} error(s) ==="
[ "${errors}" -eq 0 ]
