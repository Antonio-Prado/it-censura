#!/bin/sh -e
BASE=/root/censorship/tmp
CONF_DIR=/usr/local/etc/unbound/blacklists.d
OUT=$CONF_DIR/BL_all.conf
TMP_COMMUNITY=$(mktemp)
TMP_OFFICIAL=$(mktemp)

# Estrai i nomi dominio dalle liste ufficiali/manuale (normalizzati in lowercase)
awk '/^local-zone:/ { dom=$2; gsub(/"/, "", dom); gsub(/\.$/, "", dom); print tolower(dom) }' \
    $CONF_DIR/db.blacklist_*.conf 2>/dev/null | sort -u > $TMP_OFFICIAL

# Aggiungi la whitelist all'insieme di esclusione (domini da non bloccare)
WHITELIST="$(dirname $BASE)/whitelist.txt"
if [ -f "$WHITELIST" ] && [ -s "$WHITELIST" ]; then
    grep -vE '^[[:space:]]*(#|$)' "$WHITELIST" | awk '{print tolower($0)}' \
        | cat - "$TMP_OFFICIAL" | sort -u > "${TMP_OFFICIAL}.tmp"
    mv "${TMP_OFFICIAL}.tmp" "$TMP_OFFICIAL"
fi

# Raccogli le entry community:
# - filtra domini malformati (iniziano con carattere non valido)
# - normalizza in lowercase (elimina duplicati case-insensitive)
# - sort|uniq per deduplicazione efficiente su disco (no RAM per 39M righe)
cat $BASE/BL_* \
    | grep -vE '^local-zone: [^a-zA-Z0-9"*]' \
    | awk '{gsub(/\. /, " "); print tolower($0)}' \
    | sort | uniq \
    > $TMP_COMMUNITY

echo 'server:' > $OUT

# Filtra dall'output community i domini già presenti nelle liste ufficiali.
# L'array excl è piccolo (~10K voci), nessun problema di memoria.
awk 'NR==FNR { excl[$0]=1; next }
     /^local-zone:/ { dom=$2; gsub(/"/, "", dom); gsub(/\.$/, "", dom); if (dom in excl) next }
     1' "$TMP_OFFICIAL" "$TMP_COMMUNITY" >> $OUT

rm -f $TMP_COMMUNITY $TMP_OFFICIAL
