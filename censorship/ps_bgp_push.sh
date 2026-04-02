#!/bin/sh
# ps_bgp_push.sh — Carica gli IP Piracy Shield nel daemon BGP (OpenBGPD).
#
# Legge i file IPv4 e IPv6 generati da ps_sync.py e li annuncia via bgpctl
# con community NO_EXPORT e BLACKHOLE.
#
# Variabili d'ambiente:
#   PS_IPV4_FILE   File IPv4 (default: /etc/unbound/blacklists/ps_ipv4.txt)
#   PS_IPV6_FILE   File IPv6 (default: /etc/unbound/blacklists/ps_ipv6.txt)

PS_IPV4_FILE="${PS_IPV4_FILE:-/etc/unbound/blacklists/ps_ipv4.txt}"
PS_IPV6_FILE="${PS_IPV6_FILE:-/etc/unbound/blacklists/ps_ipv6.txt}"

# Svuota le reti precedentemente annunciate
bgpctl network flush

# Annuncia gli IPv4
if [ -f "${PS_IPV4_FILE}" ]; then
    while IFS= read -r ip; do
        [ -z "${ip}" ] && continue
        bgpctl network add "${ip}" localpref 120 community NO_EXPORT community BLACKHOLE
    done < "${PS_IPV4_FILE}"
    echo "IPv4 caricati in BGP da: ${PS_IPV4_FILE}" >&2
else
    echo "Attenzione: file IPv4 non trovato: ${PS_IPV4_FILE}" >&2
fi

# Annuncia gli IPv6
if [ -f "${PS_IPV6_FILE}" ]; then
    while IFS= read -r ip; do
        [ -z "${ip}" ] && continue
        bgpctl network add "${ip}" localpref 120 community NO_EXPORT community BLACKHOLE
    done < "${PS_IPV6_FILE}"
    echo "IPv6 caricati in BGP da: ${PS_IPV6_FILE}" >&2
else
    echo "Attenzione: file IPv6 non trovato: ${PS_IPV6_FILE}" >&2
fi
