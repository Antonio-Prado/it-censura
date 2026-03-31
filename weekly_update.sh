#!/bin/sh
# Wrapper cron settimanale: aggiorna le blacklist
# Sostituisce la voce cron diretta a update_blacklists.sh

CENSORSHIP_ROOT="${CENSORSHIP_ROOT:-/root/censorship}"
LOGFILE="/var/log/censorship_update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOGFILE}"; }

log "=== Avvio aggiornamento settimanale blacklist ==="

log "--- update_blacklists.sh ---"
sh "${CENSORSHIP_ROOT}/update_blacklists.sh" >> "${LOGFILE}" 2>&1
rc=$?
if [ ${rc} -ne 0 ]; then
    log "ATTENZIONE: update_blacklists.sh terminato con codice ${rc}"
else
    log "update_blacklists.sh completato."
fi

log "--- BL_concat.sh ---"
sh "${CENSORSHIP_ROOT}/BL_concat.sh" >> "${LOGFILE}" 2>&1
rc=$?
if [ ${rc} -ne 0 ]; then
    log "ATTENZIONE: BL_concat.sh terminato con codice ${rc}"
else
    log "BL_concat.sh completato."
fi

log "=== Fine aggiornamento settimanale ==="
