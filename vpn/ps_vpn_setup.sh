#!/bin/sh
# ps_vpn_setup.sh — Configura la VPN site-to-site verso Piracy Shield (FreeBSD + StrongSwan).
#
# Genera /usr/local/etc/ipsec.conf e /usr/local/etc/ipsec.secrets a partire
# dalle variabili d'ambiente, installa StrongSwan se assente, abilita il
# servizio in rc.conf e avvia (o riavvia) la connessione.
#
# Eseguire come root.
#
# Variabili d'ambiente — parametri ISP (forniti all'AGCOM):
#   VPN_LOCAL_IP      IP pubblico del dispositivo VPN dell'ISP
#   VPN_LOCAL_NET     Rete on-premises dell'ISP in notazione CIDR (es. 192.168.0.0/24)
#
# Variabili d'ambiente — parametri Azure (forniti da AGCOM):
#   VPN_AZURE_GW_IP   IP del gateway VPN Azure
#   VPN_AZURE_NET     Rete virtuale Azure in notazione CIDR (es. 10.0.0.0/16)
#   VPN_PSK           Chiave condivisa (Pre-Shared Key)

set -e

# ── Verifica variabili obbligatorie ────────────────────────────────────────────
for VAR in VPN_LOCAL_IP VPN_LOCAL_NET VPN_AZURE_GW_IP VPN_AZURE_NET VPN_PSK; do
    eval VAL=\$$VAR
    if [ -z "${VAL}" ]; then
        echo "Errore: variabile d'ambiente ${VAR} non impostata." >&2
        exit 1
    fi
done

# ── Installazione StrongSwan ───────────────────────────────────────────────────
if ! command -v ipsec >/dev/null 2>&1; then
    echo "StrongSwan non trovato. Installazione in corso..."
    pkg install -y strongswan
fi

# ── Moduli kernel IPsec ────────────────────────────────────────────────────────
# Il supporto IPsec è incluso nel kernel GENERIC di FreeBSD.
# Verifica che sia disponibile; in caso contrario carica il modulo.
if ! kldstat -q -n ipsec 2>/dev/null; then
    kldload ipsec 2>/dev/null || true
fi

# ── Generazione ipsec.conf ─────────────────────────────────────────────────────
cat > /usr/local/etc/ipsec.conf << EOF
# Generato da ps_vpn_setup.sh — non modificare manualmente.
# Rigenerare eseguendo nuovamente lo script.

config setup
    charondebug="ike 1, knl 1, cfg 0"
    uniqueids=no

conn piracy-shield
    type=tunnel
    auto=start
    keyexchange=ikev2
    authby=secret

    # Lato ISP
    left=%defaultroute
    leftid=${VPN_LOCAL_IP}
    leftsubnet=${VPN_LOCAL_NET}

    # Lato Azure (Piracy Shield)
    right=${VPN_AZURE_GW_IP}
    rightid=${VPN_AZURE_GW_IP}
    rightsubnet=${VPN_AZURE_NET}

    # Algoritmi crittografici compatibili con Azure VPN Gateway
    ike=aes256-sha256-modp2048!
    esp=aes256-sha256-modp2048!

    # Durate
    ikelifetime=28800s
    lifetime=3600s
    margintime=540s

    # Dead Peer Detection: riconnette automaticamente se il tunnel cade
    dpdaction=restart
    dpddelay=30s
    dpdtimeout=120s
EOF

chmod 600 /usr/local/etc/ipsec.conf
echo "ipsec.conf scritto."

# ── Generazione ipsec.secrets ──────────────────────────────────────────────────
cat > /usr/local/etc/ipsec.secrets << EOF
# Generato da ps_vpn_setup.sh — non modificare manualmente.
${VPN_LOCAL_IP} ${VPN_AZURE_GW_IP} : PSK "${VPN_PSK}"
EOF

chmod 600 /usr/local/etc/ipsec.secrets
echo "ipsec.secrets scritto."

# ── Abilitazione servizio in rc.conf ──────────────────────────────────────────
if ! grep -q 'strongswan_enable' /etc/rc.conf; then
    echo 'strongswan_enable="YES"' >> /etc/rc.conf
    echo "strongswan_enable aggiunto a /etc/rc.conf."
fi

# ── Avvio / riavvio ───────────────────────────────────────────────────────────
if service strongswan status >/dev/null 2>&1; then
    echo "Riavvio StrongSwan..."
    service strongswan restart
else
    echo "Avvio StrongSwan..."
    service strongswan start
fi

echo ""
echo "Configurazione VPN completata."
echo "Verifica lo stato del tunnel con: sh vpn/ps_vpn_check.sh"
