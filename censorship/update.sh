#!/bin/sh
# update.sh — Scarica e converte tutte le blacklist ufficiali italiane.
#
# Ogni lista viene scaricata (dove applicabile) e convertita in formato
# Unbound local-zone tramite parse.py.  Gli errori su singole liste vengono
# registrati ma non interrompono l'esecuzione complessiva.
#
# Variabili d'ambiente:
#
#   BL_DIR      Directory di lavoro per i file scaricati  (default: /etc/unbound/blacklists)
#   CONF_DIR    Directory include di Unbound              (default: /usr/local/etc/unbound/blacklists.d)
#   WHITELIST   File whitelist                            (default: $BL_DIR/whitelist.txt)
#
#   CNCPO_FILE  Percorso del file CSV CNCPO.
#               La lista CNCPO non è scaricabile pubblicamente: viene distribuita
#               agli ISP tramite canale dedicato.
#
#   AAMS_URL    URL della blacklist ADM giochi (lista in formato testo).
#   ADMT_URL    URL della blacklist ADM tabacchi (lista in formato testo).
#               Pubblicate dall'Agenzia delle Dogane e dei Monopoli.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
CONF_DIR="${CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
WHITELIST="${WHITELIST:-${BL_DIR}/whitelist.txt}"

mkdir -p "${BL_DIR}" "${CONF_DIR}"

log()  { printf '[%s] %s\n'          "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
warn() { printf '[%s] ATTENZIONE: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

errors=0

# Esegue parse.py su un file di input e scrive <nome>.conf in CONF_DIR.
parse() {
    fmt="$1"; infile="$2"; name="$3"
    wl_opt=
    [ -f "${WHITELIST}" ] && wl_opt="-w ${WHITELIST}"
    # shellcheck disable=SC2086
    python3 "${SCRIPT_DIR}/parse.py" \
        -f "${fmt}" -i "${infile}" -o "${CONF_DIR}/${name}.conf" ${wl_opt}
}

# ── CNCPO — pedopornografia (non scaricabile pubblicamente) ───────────────────
if [ -n "${CNCPO_FILE}" ] && [ -f "${CNCPO_FILE}" ]; then
    log "--- CNCPO ---"
    parse cncpo "${CNCPO_FILE}" CNCPO \
        || { warn "CNCPO: aggiornamento fallito"; errors=$((errors + 1)); }
else
    log "CNCPO: CNCPO_FILE non impostato o non trovato — saltato."
fi

# ── AAMS — ADM giochi online ──────────────────────────────────────────────────
if [ -n "${AAMS_URL}" ]; then
    log "--- AAMS ---"
    RAW="${BL_DIR}/raw_aams.txt"
    curl -fsSL --max-time 60 "${AAMS_URL}" -o "${RAW}" \
        && parse plain "${RAW}" AAMS \
        || { warn "AAMS: aggiornamento fallito"; errors=$((errors + 1)); }
else
    log "AAMS: AAMS_URL non impostato — saltato."
fi

# ── ADMT — ADM tabacchi ───────────────────────────────────────────────────────
if [ -n "${ADMT_URL}" ]; then
    log "--- ADMT ---"
    RAW="${BL_DIR}/raw_admt.txt"
    curl -fsSL --max-time 60 "${ADMT_URL}" -o "${RAW}" \
        && parse plain "${RAW}" ADMT \
        || { warn "ADMT: aggiornamento fallito"; errors=$((errors + 1)); }
else
    log "ADMT: ADMT_URL non impostato — saltato."
fi

# ── AGCOM — diritto d'autore ──────────────────────────────────────────────────
log "--- AGCOM ---"
RAW="${BL_DIR}/raw_agcom.bin"
python3 "${SCRIPT_DIR}/download_agcom.py" -o "${RAW}" \
    && parse plain "${RAW}" AGCOM \
    || { warn "AGCOM: aggiornamento fallito"; errors=$((errors + 1)); }

# ── CONSOB — abusivismo finanziario ──────────────────────────────────────────
log "--- CONSOB ---"
RAW="${BL_DIR}/raw_consob.txt"
python3 "${SCRIPT_DIR}/download_consob.py" -o "${RAW}" \
    && parse plain "${RAW}" CONSOB \
    || { warn "CONSOB: aggiornamento fallito"; errors=$((errors + 1)); }

# ── IVASS — intermediari assicurativi abusivi ─────────────────────────────────
log "--- IVASS ---"
RAW="${BL_DIR}/raw_ivass.txt"
python3 "${SCRIPT_DIR}/download_ivass.py" -o "${RAW}" \
    && parse plain "${RAW}" IVASS \
    || { warn "IVASS: aggiornamento fallito"; errors=$((errors + 1)); }

log "=== Aggiornamento completato — ${errors} errore/i ==="
[ "${errors}" -eq 0 ]
