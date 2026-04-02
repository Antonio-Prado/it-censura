#!/bin/sh
# Script di aggiornamento settimanale delle blacklist — adattare UPDATE_CMD all'ambiente
# Esempio di voce cron: 0 3 * * 0 /path/to/weekly_update.sh

LOGFILE="/var/log/dns_gui_update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOGFILE}"; }

log "=== Aggiornamento blacklist avviato ==="

# Impostare UPDATE_CMD con il comando che scarica/rigenera le blacklist, es.:
#   UPDATE_CMD="sh /opt/blacklists/update.sh"
if [ -z "${UPDATE_CMD}" ]; then
    log "ERRORE: UPDATE_CMD non impostato. Modificare lo script o impostare la variabile d'ambiente."
    exit 1
fi

eval "${UPDATE_CMD}" >> "${LOGFILE}" 2>&1
rc=$?
if [ ${rc} -ne 0 ]; then
    log "ERRORE: UPDATE_CMD terminato con codice ${rc}"
else
    log "Aggiornamento completato."
fi

log "=== Aggiornamento blacklist terminato ==="
exit ${rc}
