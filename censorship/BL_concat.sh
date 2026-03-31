#!/bin/sh
# BL_concat.sh — Unisce le blacklist ufficiali e quelle community in un unico
# file .conf, deduplicando le voci e applicando la whitelist.
#
# Usare questo script se si affiancano liste community alle liste ufficiali.
# Rimuove dalle liste community qualsiasi dominio già coperto dalle liste
# ufficiali o dalla whitelist.
#
# Variabili d'ambiente:
#   BL_DIR           Directory contenente i file community (default: /etc/unbound/blacklists)
#   CONF_DIR         Directory include di Unbound, dove risiedono i .conf ufficiali
#                    (default: /usr/local/etc/unbound/blacklists.d)
#   WHITELIST        File whitelist (default: $BL_DIR/whitelist.txt)
#   OUT_FILE         File di output (default: $CONF_DIR/merged.conf)
#   COMMUNITY_GLOB   Pattern dei file community in BL_DIR (default: BL_*.conf)

BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
CONF_DIR="${CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
WHITELIST="${WHITELIST:-${BL_DIR}/whitelist.txt}"
OUT="${OUT_FILE:-${CONF_DIR}/merged.conf}"
COMMUNITY_GLOB="${COMMUNITY_GLOB:-BL_*.conf}"

TMP_EXCL=$(mktemp)
TMP_COMM=$(mktemp)
trap 'rm -f "${TMP_EXCL}" "${TMP_COMM}"' EXIT

# Costruisce l'insieme di esclusione: domini dai .conf ufficiali + whitelist.
# Esclude il file di output (OUT) per evitare duplicati nelle esecuzioni successive.
find "${CONF_DIR}" -maxdepth 1 -name '*.conf' ! -path "${OUT}" -print \
    | xargs awk '/^local-zone:/ {
        dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom); print tolower(dom)
    }' 2>/dev/null | sort -u > "${TMP_EXCL}"

if [ -f "${WHITELIST}" ] && [ -s "${WHITELIST}" ]; then
    grep -vE '^[[:space:]]*(#|$)' "${WHITELIST}" \
        | awk '{print tolower($0)}' \
        | cat - "${TMP_EXCL}" | sort -u > "${TMP_EXCL}.new"
    mv "${TMP_EXCL}.new" "${TMP_EXCL}"
fi

# Raccoglie le voci community filtrando i domini malformati
if ls "${BL_DIR}"/${COMMUNITY_GLOB} 2>/dev/null | grep -q .; then
    cat "${BL_DIR}"/${COMMUNITY_GLOB} \
        | grep -vE '^local-zone: [^a-zA-Z0-9"*]' \
        | awk '{gsub(/\. /, " "); print tolower($0)}' \
        | sort | uniq \
        > "${TMP_COMM}"
else
    : > "${TMP_COMM}"
fi

# Scrive l'output: voci ufficiali prima, poi community meno le escluse
{
    printf 'server:\n'
    find "${CONF_DIR}" -maxdepth 1 -name '*.conf' ! -path "${OUT}" -print \
        | xargs grep '^local-zone:' 2>/dev/null \
        | awk '{gsub(/\. /, " "); print tolower($0)}' \
        | sort -u
    awk 'NR==FNR { excl[$0]=1; next }
         /^local-zone:/ {
             dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom)
             if (!(dom in excl)) print
         }' "${TMP_EXCL}" "${TMP_COMM}"
} > "${OUT}"

echo "File unificato scritto in: ${OUT}" >&2
