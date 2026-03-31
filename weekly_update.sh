#!/bin/sh
# Weekly blacklist update script — adapt UPDATE_CMD to your environment
# Example cron entry: 0 3 * * 0 /path/to/weekly_update.sh

LOGFILE="/var/log/dns_gui_update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOGFILE}"; }

log "=== Blacklist update started ==="

# Set UPDATE_CMD to the command that fetches/regenerates your blacklists, e.g.:
#   UPDATE_CMD="sh /opt/blacklists/update.sh"
if [ -z "${UPDATE_CMD}" ]; then
    log "ERROR: UPDATE_CMD is not set. Edit this script or set the env var."
    exit 1
fi

eval "${UPDATE_CMD}" >> "${LOGFILE}" 2>&1
rc=$?
if [ ${rc} -ne 0 ]; then
    log "ERROR: UPDATE_CMD exited with code ${rc}"
else
    log "Update completed."
fi

log "=== Blacklist update finished ==="
exit ${rc}
