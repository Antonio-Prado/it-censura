#!/bin/sh
# community.sh — Scarica le liste community di malware, C2 e phishing e le converte in
# file BL_<nome>.conf in BL_DIR, pronti per BL_concat.sh.
#
# Fonti:
#   urlhaus        abuse.ch URLhaus: domini che distribuiscono malware (RPZ ufficiale).
#                  Senza chiave abuse.ch usa il mirror malware-filter/urlhaus-filter (CC0/MIT).
#   threatfox      abuse.ch ThreatFox: domini di server C2, consegna payload, botnet.
#                  Richiede la chiave abuse.ch: senza chiave viene saltata.
#   scamblocklist  durablenapkin/scamblocklist (MIT): truffe online.
#   phishdomains   romainmarcoux/malicious-domains (MIT): aggregato di fonti di phishing.
#
# Fonti opzionali HaGeZi (https://github.com/hagezi/dns-blocklists, GPL-3.0, aggiornate piu'
# volte al giorno), da aggiungere a COMMUNITY_LISTS:
#   hagezi-tif      Threat Intelligence Feeds: malware, phishing, truffe, cryptojacking, C2.
#                   Variante con HAGEZI_TIF: tif.mini (~190k), tif.medium (~410k, default),
#                   tif (~2,2 milioni: richiede molta RAM per Unbound).
#   hagezi-multi    pubblicita', tracker, telemetria ("Multi normal", ~190k)
#   hagezi-piracy   siti e servizi di pirateria (anti.piracy, ~60k)
#   hagezi-smarttv  telemetria delle smart TV LG webOS, Samsung, Roku
# hagezi-multi, hagezi-piracy e hagezi-smarttv filtrano CONTENUTI, non minacce: vanno bene per la
# rete interna di un ente o di un'azienda. Un operatore che offre accesso a internet a terzi deve
# valutare il Regolamento UE 2015/2120 (art. 3, par. 3), che limita i blocchi a obblighi di legge,
# sicurezza e integrita' della rete, congestione. Per queste tre il filtro Tranco e' disattivato:
# i domini pubblicitari e di pirateria sono popolari per natura.
#
# Ogni fonte passa da community_filter.py, che scarta IP, nomi malformati, .gov.it, suffissi
# pubblici (Public Suffix List) e i domini fra i primi N di Tranco (falsi positivi probabili).
# Se un download fallisce o la lista e' anomala, il BL_<nome>.conf precedente resta intatto:
# un errore non svuota mai una lista.
#
# Chiave abuse.ch: account gratuito su https://auth.abuse.ch/ -> "Auth Key". Va salvata in un
# file leggibile solo da root (ABUSECH_KEY_FILE), mai nel repository. Le API di export
# accettano la chiave solo nell'URL: viene passata a curl via stdin (curl -K -), cosi' non
# compare nell'elenco dei processi ne' nei log. Uso commerciale: verificare i termini di
# abuse.ch (fair use; "commercial or for-profit needs may require a paid subscription").
#
# Variabili d'ambiente:
#   BL_DIR            Directory dei file community   (default: /etc/unbound/blacklists)
#   ABUSECH_KEY_FILE  File con la chiave abuse.ch    (default: $BL_DIR/.abusech_authkey)
#   COMMUNITY_LISTS   Fonti da aggiornare            (default: "urlhaus threatfox scamblocklist phishdomains")
#   TRANCO_TOP        Siti popolari esclusi (top N)  (default: 100000; 0 = nessuna esclusione)
#   HAGEZI_TIF        Variante di hagezi-tif         (default: tif.medium)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BL_DIR="${BL_DIR:-/etc/unbound/blacklists}"
KEY_FILE="${ABUSECH_KEY_FILE:-${BL_DIR}/.abusech_authkey}"
LISTS="${COMMUNITY_LISTS:-urlhaus threatfox scamblocklist phishdomains}"
TOP="${TRANCO_TOP:-100000}"
HAGEZI_TIF="${HAGEZI_TIF:-tif.medium}"
HAGEZI_BASE=https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard
export BL_DIR

mkdir -p "${BL_DIR}"

log()  { printf '[%s] %s\n'          "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
warn() { printf '[%s] ATTENZIONE: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

errors=0

# Scarica un export abuse.ch con la chiave nell'URL, senza esporla. $1 host, $2 file, $3 output
abusech_get() {
    code=$(printf 'url = "https://%s/v2/files/exports/%s/%s"\n' "$1" "$(cat "${KEY_FILE}")" "$2" \
        | curl -s -m 180 -K - -o "$3" -w '%{http_code}')
    [ "${code}" = "200" ]
}

# Applica il filtro comune. $1 nome, $2 formato (hosts|domains), $3 file scaricato, $4 minimo domini,
# $5 (opzionale) siti popolari esclusi, al posto di TRANCO_TOP
filter() {
    python3 "${SCRIPT_DIR}/community_filter.py" "$1" "$2" "$3" --min "$4" --top "${5:-${TOP}}"
}

# Scarica una o piu' liste HaGeZi "wildcard onlydomains" e le concatena in $1.
# Ogni file deve contenere "# Number of entries" (scarta risposte troncate o pagine d'errore).
hagezi_get() {
    out="$1"; shift
    : > "${out}"
    for f in "$@"; do
        curl -fsSL --max-time 180 "${HAGEZI_BASE}/${f}-onlydomains.txt" -o "${out}.part" || return 1
        grep -q '^# Number of entries' "${out}.part" || return 1
        cat "${out}.part" >> "${out}"
    done
    rm -f "${out}.part"
}

has_key() { [ -s "${KEY_FILE}" ]; }

for name in ${LISTS}; do
    log "--- ${name} ---"
    NEW="${BL_DIR}/${name}.raw.new"
    rm -f "${NEW}"
    ok=1
    case "${name}" in
        urlhaus)
            if has_key && abusech_get urlhaus-api.abuse.ch rpz.txt "${NEW}" \
                    && grep -q 'URLhaus Response Policy Zones' "${NEW}"; then
                # RPZ: "dominio CNAME . ; commento" -> un dominio per riga, senza la voce di test
                cut -d' ' -f1 "${NEW}" | grep -vE '^(;|\$|@|$)' \
                    | grep -v '^testentry\.rpz\.urlhaus\.abuse\.ch' > "${NEW}.d" && mv "${NEW}.d" "${NEW}"
            else
                has_key && warn "urlhaus: fonte ufficiale non disponibile, uso il mirror"
                rm -f "${NEW}"
                for url in https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-domains-online.txt \
                           https://curbengh.github.io/malware-filter/urlhaus-filter-domains-online.txt; do
                    curl -fsSL --max-time 120 "${url}" -o "${NEW}" && [ -s "${NEW}" ] && break
                    rm -f "${NEW}"
                done
            fi
            [ -s "${NEW}" ] && filter urlhaus domains "${NEW}" 100 || ok=0
            ;;
        threatfox)
            if ! has_key; then
                log "threatfox: chiave abuse.ch assente (${KEY_FILE}) — saltato."
                continue
            fi
            abusech_get threatfox-api.abuse.ch hostfile.txt "${NEW}" \
                && grep -q 'ThreatFox IOCs: host file' "${NEW}" \
                && filter threatfox hosts "${NEW}" 5000 || ok=0
            ;;
        scamblocklist)
            curl -fsSL --max-time 120 \
                https://raw.githubusercontent.com/durablenapkin/scamblocklist/master/hosts.txt -o "${NEW}" \
                && filter scamblocklist hosts "${NEW}" 500 || ok=0
            ;;
        phishdomains)
            # file a blocchi di 131072 voci (full-domains-aa, ab, ...): ci si ferma al primo vuoto
            : > "${NEW}"
            for part in aa ab ac ad ae af; do
                curl -fsSL --max-time 120 \
                    "https://raw.githubusercontent.com/romainmarcoux/malicious-domains/main/full-domains-${part}.txt" \
                    -o "${NEW}.part" || break
                [ "$(grep -c . "${NEW}.part")" -gt 1 ] || break
                cat "${NEW}.part" >> "${NEW}"
            done
            rm -f "${NEW}.part"
            [ -s "${NEW}" ] && filter phishdomains domains "${NEW}" 50000 || ok=0
            ;;
        hagezi-tif)
            case "${HAGEZI_TIF}" in tif.mini) min=100000 ;; tif) min=1000000 ;; *) min=200000 ;; esac
            hagezi_get "${NEW}" "${HAGEZI_TIF}" && filter hagezi-tif domains "${NEW}" "${min}" || ok=0
            ;;
        hagezi-multi)
            hagezi_get "${NEW}" multi && filter hagezi-multi domains "${NEW}" 80000 0 || ok=0
            ;;
        hagezi-piracy)
            hagezi_get "${NEW}" anti.piracy && filter hagezi-piracy domains "${NEW}" 30000 0 || ok=0
            ;;
        hagezi-smarttv)
            hagezi_get "${NEW}" native.lgwebos native.samsung native.roku \
                && filter hagezi-smarttv domains "${NEW}" 300 0 || ok=0
            ;;
        *)
            warn "${name}: fonte sconosciuta"
            ok=0
            ;;
    esac
    rm -f "${NEW}" "${NEW}.part"
    if [ "${ok}" -ne 1 ]; then
        warn "${name}: aggiornamento fallito, resta la lista precedente"
        errors=$((errors + 1))
    fi
done

log "=== Liste community aggiornate — ${errors} errore/i ==="
[ "${errors}" -eq 0 ]
