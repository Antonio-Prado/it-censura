#!/bin/sh
# Sincronizzazione blacklist Unbound da ns1-rec a ns2-rec
# Usato sia dalla GUI che dal cron settimanale

NS2_HOST="${NS2_HOST:-ns2-rec.as59715.net}"
NS2_PORT="${NS2_SSH_PORT:-911}"
NS2_USER="${NS2_SSH_USER:-unbound-sync}"
NS2_KEY="${NS2_SSH_KEY:-/root/.ssh/id_ns2}"
CONF_DIR="${UNBOUND_CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
UNBOUND_SERVICE="${UNBOUND_SERVICE:-unbound}"

SSH_OPTS="-p ${NS2_PORT} -i ${NS2_KEY} -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

echo "[sync_ns2] Inizio sincronizzazione verso ${NS2_USER}@${NS2_HOST}..."

rsync -az --delete \
    --rsync-path="sudo rsync" \
    -e "ssh ${SSH_OPTS}" \
    "${CONF_DIR}/" \
    "${NS2_USER}@${NS2_HOST}:${CONF_DIR}/" || {
    echo "[sync_ns2] ERRORE: rsync fallito (exit $?)"
    exit 1
}

echo "[sync_ns2] rsync completato. Ricarico Unbound su ns2..."

# shellcheck disable=SC2086
ssh ${SSH_OPTS} "${NS2_USER}@${NS2_HOST}" sudo service "${UNBOUND_SERVICE}" reload || {
    echo "[sync_ns2] ERRORE: reload Unbound su ns2 fallito (exit $?)"
    exit 1
}

echo "[sync_ns2] Sincronizzazione completata."
