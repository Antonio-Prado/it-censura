#!/bin/sh
# BL_concat.sh — Merge official and community blacklists into a single
# BL_all.conf, deduplicating entries and applying the whitelist.
#
# Use this script if you run community lists (BL_*.conf) alongside the
# official ones.  It removes from community lists any domain already
# covered by an official list or by the whitelist.
#
# Configuration via environment variables:
#   BL_DIR    Directory containing community BL_*.conf files
#             (default: /etc/unbound/blacklists)
#   CONF_DIR  Unbound include directory; official *.conf files live here
#             and BL_all.conf is written here
#             (default: /usr/local/etc/unbound/blacklists.d)
#   WHITELIST Whitelist file  (default: $BL_DIR/whitelist.txt)

BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
CONF_DIR="${CONF_DIR:-/usr/local/etc/unbound/blacklists.d}"
WHITELIST="${WHITELIST:-${BL_DIR}/whitelist.txt}"
OUT="${CONF_DIR}/BL_all.conf"

TMP_EXCL=$(mktemp)
TMP_COMM=$(mktemp)
trap 'rm -f "${TMP_EXCL}" "${TMP_COMM}"' EXIT

# Build exclusion set: domains from official conf files + whitelist
awk '/^local-zone:/ {
    dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom); print tolower(dom)
}' "${CONF_DIR}"/*.conf 2>/dev/null | sort -u > "${TMP_EXCL}"

if [ -f "${WHITELIST}" ] && [ -s "${WHITELIST}" ]; then
    grep -vE '^[[:space:]]*(#|$)' "${WHITELIST}" \
        | awk '{print tolower($0)}' \
        | cat - "${TMP_EXCL}" | sort -u > "${TMP_EXCL}.new"
    mv "${TMP_EXCL}.new" "${TMP_EXCL}"
fi

# Collect community entries, filtering malformed domains
if ls "${BL_DIR}"/BL_*.conf 2>/dev/null | grep -q .; then
    cat "${BL_DIR}"/BL_*.conf \
        | grep -vE '^local-zone: [^a-zA-Z0-9"*]' \
        | awk '{gsub(/\. /, " "); print tolower($0)}' \
        | sort | uniq \
        > "${TMP_COMM}"
else
    : > "${TMP_COMM}"
fi

# Write output: official entries first, then community minus exclusions
{
    printf 'server:\n'
    grep '^local-zone:' "${CONF_DIR}"/*.conf 2>/dev/null \
        | awk '{gsub(/\. /, " "); print tolower($0)}' \
        | sort -u
    awk 'NR==FNR { excl[$0]=1; next }
         /^local-zone:/ {
             dom = $2; gsub(/"/, "", dom); gsub(/\.$/, "", dom)
             if (!(dom in excl)) print
         }' "${TMP_EXCL}" "${TMP_COMM}"
} > "${OUT}"

echo "BL_all.conf written to ${OUT}" >&2
