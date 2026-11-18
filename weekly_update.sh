#!/bin/sh
# Script di aggiornamento settimanale delle blacklist — adattare UPDATE_CMD all'ambiente
# Esempio di voce cron: 0 3 * * 0 /path/to/weekly_update.sh
#
# A fine giro cerca nel log, SOLO nelle righe di questo giro, errori e avvisi (download falliti,
# liste tenute alla versione precedente, errori Python, avvisi di unbound-checkconf) e controlla
# che il resolver locale risponda. Se trova qualcosa e ALERT_TO e' impostato, manda una mail con
# notify_mail.py (stesse variabili SMTP_* dell'interfaccia web). Senza problemi non manda nulla.
#
# Variabili d'ambiente:
#   UPDATE_CMD   comando di aggiornamento (obbligatorio), es. "sh /opt/it-censura/censorship/update.sh"
#   ALERT_TO     destinatari degli avvisi, separati da virgola (opzionale)
#   CHECK_NAME   nome risolto per verificare il resolver (default: www.google.com; vuoto = nessun controllo)
#   LOGFILE      file di log (default: /var/log/dns_gui_update.log)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGFILE="${LOGFILE:-/var/log/dns_gui_update.log}"
CHECK_NAME="${CHECK_NAME-www.google.com}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOGFILE}"; }

# prima riga di questo giro nel log
START_LINE=$(( $( (wc -l < "${LOGFILE}") 2>/dev/null || echo 0 ) + 1 ))

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

if [ -n "${CHECK_NAME}" ]; then
    if [ -z "$(dig +short +time=3 +tries=2 @127.0.0.1 "${CHECK_NAME}" A 2>/dev/null)" ]; then
        log "ERRORE: il resolver locale non risolve ${CHECK_NAME}"
    else
        log "Resolver locale attivo e risolve."
    fi
fi

log "=== Aggiornamento blacklist terminato ==="

if [ -n "${ALERT_TO}" ]; then
    ISSUES=$(tail -n +"${START_LINE}" "${LOGFILE}" \
        | grep -E 'ATTENZIONE|ERRORE|ERROR [0-9]{3}|Traceback|[Ee]rror:|warning:|fatal|failed|No such file' \
        | grep -v 'errori: 0' | sort | uniq -c | sort -rn | head -60)
    if [ -n "${ISSUES}" ]; then
        {
            echo "Aggiornamento blacklist su $(hostname) del $(date '+%Y-%m-%d %H:%M'):"
            echo "sono stati trovati errori o avvisi (numero di occorrenze, messaggio)."
            echo
            echo "${ISSUES}"
            echo
            echo "Log completo: ${LOGFILE} (dalla riga ${START_LINE})."
        } | python3 "${SCRIPT_DIR}/notify_mail.py" "[it-censura] avvisi aggiornamento $(date '+%Y-%m-%d')" \
            >> "${LOGFILE}" 2>&1 \
            || log "ERRORE: invio della mail di notifica fallito"
    fi
fi

exit ${rc}
