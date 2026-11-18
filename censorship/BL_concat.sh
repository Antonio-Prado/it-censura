#!/bin/sh
# BL_concat.sh — Unisce le blacklist ufficiali e quelle community in un unico
# file .conf, deduplicando le voci e applicando la whitelist.
#
# Usare questo script se si affiancano liste community alle liste ufficiali.
# Rimuove dalle liste community:
#   - i domini già presenti nelle liste ufficiali o nella whitelist;
#   - i sottodomini di un dominio già bloccato da una lista ufficiale (altrimenti la voce
#     community, più specifica, scavalcherebbe il blocco del padre, es. con redirect diversi).
#     La whitelist NON conta come padre: whitelistare un dominio non sblocca i suoi sottodomini.
# Il file di output viene scritto in modo atomico e reso leggibile al gruppo unbound:
# Unbound rilegge la configurazione al reload come utente non privilegiato.
#
# Variabili d'ambiente:
#   BL_DIR           Directory contenente i file community (default: /etc/unbound/blacklists)
#   CONF_DIR         Directory include di Unbound, dove risiedono i .conf ufficiali
#                    (default: /usr/local/etc/unbound/blacklists.d)
#   WHITELIST        File whitelist (default: $BL_DIR/whitelist.txt)
#   OUT_FILE         File di output (default: $CONF_DIR/merged.conf)
#   COMMUNITY_GLOB   Pattern dei file community in BL_DIR (default: BL_*.conf)
#   UNBOUND_GROUP    Gruppo proprietario del file di output (default: unbound)

BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
CONF_DIR="${CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
WHITELIST="${WHITELIST:-${BL_DIR}/whitelist.txt}"
OUT="${OUT_FILE:-${CONF_DIR}/merged.conf}"
COMMUNITY_GLOB="${COMMUNITY_GLOB:-BL_*.conf}"
UNBOUND_GROUP="${UNBOUND_GROUP:-unbound}"

TMP_EXCL=$(mktemp)
TMP_PAR=$(mktemp)
TMP_COMM=$(mktemp)
TMP_OUT=$(mktemp "${OUT}.XXXXXX")
trap 'rm -f "${TMP_EXCL}" "${TMP_PAR}" "${TMP_COMM}" "${TMP_OUT}"' EXIT

# Costruisce l'insieme di esclusione: domini dai .conf ufficiali + whitelist.
# Esclude il file di output (OUT) per evitare duplicati nelle esecuzioni successive.
find "${CONF_DIR}" -maxdepth 1 -name '*.conf' ! -path "${OUT}" -print \
    | xargs awk '/^local-zone:/ {
        dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom); print tolower(dom)
    }' 2>/dev/null | sort -u > "${TMP_EXCL}"

# Domini delle liste ufficiali, prima di aggiungere la whitelist: sono i "padri" che assorbono
# i sottodomini community
cp "${TMP_EXCL}" "${TMP_PAR}"

if [ -f "${WHITELIST}" ] && [ -s "${WHITELIST}" ]; then
    grep -vE '^[[:space:]]*(#|$)' "${WHITELIST}" \
        | awk '{print tolower($0)}' \
        | cat - "${TMP_EXCL}" | sort -u > "${TMP_EXCL}.new"
    mv "${TMP_EXCL}.new" "${TMP_EXCL}"
fi

# Raccoglie le voci community filtrando i domini malformati
if ls "${BL_DIR}"/${COMMUNITY_GLOB} 2>/dev/null | grep -q .; then
    # via anche i nomi riservati delle intestazioni dei file hosts (localhost, local,
    # broadcasthost, ip6-*, 0.0.0.0...): bloccarli renderebbe NXDOMAIN localhost e *.local
    cat "${BL_DIR}"/${COMMUNITY_GLOB} \
        | grep -vE '^local-zone: [^a-zA-Z0-9"*]' \
        | grep -viE '^local-zone: "?(localhost|localhost\.localdomain|localdomain|local|broadcasthost|ip6-[a-z]+|0\.0\.0\.0)\.?"? ' \
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
    awk 'FILENAME == ARGV[1] { excl[$0] = 1; next }
         FILENAME == ARGV[2] { par[$0] = 1; next }
         /^local-zone:/ {
             dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom)
             if (dom in excl) next
             n = split(dom, lab, ".")
             for (i = 2; i < n; i++) {
                 p = lab[i]; for (j = i + 1; j <= n; j++) p = p "." lab[j]
                 if (p in par) { absorbed++; next }
             }
             print
         }
         END { printf "Voci community assorbite da un padre ufficiale: %d\n", absorbed + 0 > "/dev/stderr" }' \
        "${TMP_EXCL}" "${TMP_PAR}" "${TMP_COMM}"
} > "${TMP_OUT}"

chgrp "${UNBOUND_GROUP}" "${TMP_OUT}" 2>/dev/null || true
chmod 640 "${TMP_OUT}"
mv "${TMP_OUT}" "${OUT}"

echo "File unificato scritto in: ${OUT}" >&2
