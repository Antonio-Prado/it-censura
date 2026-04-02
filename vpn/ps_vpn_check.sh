#!/bin/sh
# ps_vpn_check.sh — Verifica lo stato della VPN Piracy Shield.
#
# Controlla:
#   1. Che il servizio StrongSwan sia attivo
#   2. Che il tunnel IPsec risulti stabilito
#   3. Che la piattaforma Piracy Shield sia raggiungibile (ping all'host API)
#
# Variabili d'ambiente:
#   VPN_AZURE_GW_IP   IP del gateway VPN Azure (per verifica tunnel)
#   PS_API_HOST       Host dell'API Piracy Shield da raggiungere via ping
#                     (default: psp01.agcom.it)
#
# Codici di uscita:
#   0  tutto ok
#   1  uno o più controlli falliti

PS_API_HOST="${PS_API_HOST:-psp01.agcom.it}"
ERRORS=0

# ── 1. Servizio StrongSwan ─────────────────────────────────────────────────────
printf "Servizio StrongSwan... "
if service strongswan status >/dev/null 2>&1; then
    echo "ATTIVO"
else
    echo "FERMO"
    echo "  → Avviare con: service strongswan start" >&2
    ERRORS=$((ERRORS + 1))
fi

# ── 2. Tunnel IPsec stabilito ─────────────────────────────────────────────────
printf "Tunnel IPsec (piracy-shield)... "
if ipsec status piracy-shield 2>/dev/null | grep -q "ESTABLISHED"; then
    echo "STABILITO"
else
    echo "NON STABILITO"
    echo "  → Verificare i log con: ipsec statusall" >&2
    echo "  → Log StrongSwan: /var/log/strongswan.log (se configurato)" >&2
    ERRORS=$((ERRORS + 1))
fi

# ── 3. Raggiungibilità host API ────────────────────────────────────────────────
printf "Raggiungibilità %s... " "${PS_API_HOST}"
if ping -c 2 -W 3 "${PS_API_HOST}" >/dev/null 2>&1; then
    echo "RAGGIUNGIBILE"
else
    echo "NON RAGGIUNGIBILE"
    echo "  → La VPN è attiva? L'host è corretto? DNS funziona?" >&2
    ERRORS=$((ERRORS + 1))
fi

# ── Riepilogo ──────────────────────────────────────────────────────────────────
echo ""
if [ "${ERRORS}" -eq 0 ]; then
    echo "Tutti i controlli superati. La VPN è operativa."
    exit 0
else
    echo "${ERRORS} controllo/i fallito/i. Verificare i messaggi sopra." >&2
    exit 1
fi
