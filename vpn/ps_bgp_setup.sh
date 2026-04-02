#!/bin/sh
# ps_bgp_setup.sh — Installa e configura OpenBGPD su FreeBSD per il BGP blackhole
#                   Piracy Shield.
#
# Architettura:
#   Questo host (FreeBSD) gira OpenBGPD e fa un peering iBGP con il router
#   dell'ISP. ps_bgp_push.sh chiama bgpctl localmente per annunciare gli IP
#   Piracy Shield con community BLACKHOLE; il router li riceve e scarta il
#   traffico verso quegli indirizzi.
#
# Eseguire come root.
#
# Variabili d'ambiente:
#   BGP_ASN           AS number dell'ISP                        [obbligatoria]
#   BGP_LOCAL_IP      IP locale (router-id e sorgente sessione) [obbligatoria]
#   BGP_NEIGHBOR_IP   IP del router BGP peer (iBGP)             [obbligatoria]

set -e

# ── Verifica variabili obbligatorie ────────────────────────────────────────────
for VAR in BGP_ASN BGP_LOCAL_IP BGP_NEIGHBOR_IP; do
    eval VAL=\$$VAR
    if [ -z "${VAL}" ]; then
        echo "Errore: variabile d'ambiente ${VAR} non impostata." >&2
        exit 1
    fi
done

# ── Installazione OpenBGPD ─────────────────────────────────────────────────────
if ! command -v bgpd >/dev/null 2>&1; then
    echo "OpenBGPD non trovato. Installazione in corso..."
    pkg install -y openbgpd
fi

# ── Generazione /etc/bgpd.conf ─────────────────────────────────────────────────
cat > /etc/bgpd.conf << EOF
# Generato da ps_bgp_setup.sh — non modificare manualmente.
# Rigenerare eseguendo nuovamente lo script.

AS ${BGP_ASN}
router-id ${BGP_LOCAL_IP}

# Non modificare la routing table locale: questo host annuncia soltanto,
# non deve installare i route ricevuti.
fib-update no

group "blackhole-peers" {
    remote-as ${BGP_ASN}
    local-address ${BGP_LOCAL_IP}
    neighbor ${BGP_NEIGHBOR_IP} {
        descr "Router ISP — blackhole Piracy Shield"
        announce IPv4 unicast
        announce IPv6 unicast
    }
}

# Consenti l'annuncio di tutte le reti aggiunte via bgpctl network add
# verso i peer iBGP.
allow to ibgp
EOF

chmod 600 /etc/bgpd.conf
echo "bgpd.conf scritto."

# ── Abilitazione servizio in rc.conf ──────────────────────────────────────────
if ! grep -q 'bgpd_enable' /etc/rc.conf; then
    echo 'bgpd_enable="YES"' >> /etc/rc.conf
    echo "bgpd_enable aggiunto a /etc/rc.conf."
fi

# ── Avvio / riavvio ───────────────────────────────────────────────────────────
if service bgpd status >/dev/null 2>&1; then
    echo "Riavvio OpenBGPD..."
    service bgpd restart
else
    echo "Avvio OpenBGPD..."
    service bgpd start
fi

echo ""
echo "OpenBGPD configurato e avviato."
echo "Verifica la sessione con: bgpctl show neighbor"
echo "Verifica i route annunciati con: bgpctl show rib"
