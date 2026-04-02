# DNS Blacklist Manager

Software per la gestione della censura DNS di Stato da parte di ISP e operatori di rete italiani.

Automatizza il download e la conversione delle blacklist ufficiali imposte dalle autorità italiane
(CNCPO, ADM, AGCOM, CONSOB, IVASS), le integra nel resolver [Unbound](https://nlnetlabs.nl/projects/unbound/)
e mette a disposizione un'interfaccia web per la ricerca, il monitoraggio e la gestione manuale dei domini bloccati.

## Componenti

Il progetto è composto da due componenti indipendenti:

| Componente | Percorso | Funzione |
|------------|----------|----------|
| **Script di aggiornamento** | `censorship/` | Scaricano e convertono le blacklist ufficiali in formato Unbound |
| **Interfaccia web** | `app.py` + `templates/` | Ricerca domini, gestione lista manuale, monitoraggio liste attive |

I due componenti comunicano tramite filesystem: gli script scrivono i file `.conf` che Unbound carica,
e l'interfaccia web legge quegli stessi file.

---

## Requisiti

- Python 3.10 o superiore
- [Unbound](https://nlnetlabs.nl/projects/unbound/) come resolver DNS
- `curl` (per il download delle liste ADM in `update.sh`)
- FreeBSD o Linux

---

## Installazione

```sh
# 1. Clona il repository
git clone https://github.com/Antonio-Prado/it-censura.git
cd it-censura

# 2. Dipendenze interfaccia web
pip install flask flask-login
# oppure su FreeBSD:
# pkg install py311-flask py311-flask-login

# 3. Dipendenze script di aggiornamento
pip install requests beautifulsoup4 tldextract urlextract pypdf

# 4. Crea le directory di lavoro
mkdir -p /etc/unbound/blacklists
mkdir -p /usr/local/etc/unbound/blacklists.d
```

---

## Struttura delle directory

```
/etc/unbound/blacklists/               ← BL_DIR (directory di lavoro)
    raw_aams.txt                       ← file scaricati (temporanei)
    raw_admt.txt
    raw_agcom.bin
    raw_consob.txt
    raw_ivass.txt
    manual.txt                         ← lista manuale (gestita dall'interfaccia web)
    whitelist.txt                      ← domini esclusi da tutti i blocchi

/usr/local/etc/unbound/blacklists.d/   ← CONF_DIR (directory include di Unbound)
    CNCPO.conf
    AAMS.conf
    ADMT.conf
    AGCOM.conf
    CONSOB.conf
    IVASS.conf
    MANUAL.conf
```

---

## Configurazione di Unbound

Aggiungere in `unbound.conf` per caricare tutti i file di blocco generati:

```
server:
    include: "/usr/local/etc/unbound/blacklists.d/*.conf"
```

Ogni file `.conf` usa il tipo di risposta `always_nxdomain`:

```
server:
local-zone: "dominio-bloccato.it" always_nxdomain
...
```

---

## Liste ufficiali italiane

Gli script supportano nativamente le seguenti liste obbligatorie per legge:

| Lista | Autorità | Oggetto | Acquisizione |
|-------|----------|---------|--------------|
| `CNCPO` | Polizia Postale | Pedopornografia online | Distribuita agli ISP (non pubblica) |
| `AAMS` | ADM | Giochi online non autorizzati | URL diretto dal portale ADM |
| `ADMT` | ADM | Vendita tabacchi online | URL diretto dal portale ADM |
| `AGCOM` | AGCOM | Violazione del diritto d'autore | Scaricata dal sito AGCOM |
| `CONSOB` | CONSOB | Servizi finanziari abusivi | Estratta dal sito CONSOB |
| `IVASS` | IVASS | Intermediari assicurativi abusivi | Estratta dai PDF IVASS |
| `MANUAL` | — | Provvedimenti giudiziari e blocchi manuali | Gestita dall'interfaccia web |

---

## Avvio rapido

### 1. Creare il primo utente

```sh
python3 manage_users.py add admin
```

### 2. Aggiornare le liste ufficiali

```sh
export BL_DIR=/etc/unbound/blacklists
export CONF_DIR=/usr/local/etc/unbound/blacklists.d
export CNCPO_FILE=/percorso/lista_cncpo.csv   # fornita dal CNCPO all'ISP
export AAMS_URL=https://...                    # portale ADM
export ADMT_URL=https://...                    # portale ADM

sh censorship/update.sh
```

Lo script registra l'avanzamento su stdout. Un errore su una singola lista non blocca le altre;
il codice di uscita è non-zero se almeno una lista ha fallito.

### 3. Avviare l'interfaccia web

```sh
BL_DIR=/etc/unbound/blacklists \
CONF_DIR=/usr/local/etc/unbound/blacklists.d \
APPLY_CMD="python3 /opt/it-censura/censorship/parse.py \
    -f plain \
    -i /etc/unbound/blacklists/manual.txt \
    -o /usr/local/etc/unbound/blacklists.d/MANUAL.conf \
    && service unbound reload" \
UPDATE_CMD="sh /opt/it-censura/censorship/update.sh && service unbound reload" \
python3 app.py
```

L'interfaccia è disponibile su `http://localhost:5000`.

---

## Variabili di configurazione

Tutta la configurazione avviene tramite variabili d'ambiente. Non sono necessari file di configurazione.

### Interfaccia web (`app.py`)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `BL_DIR` | `/etc/unbound/blacklists` | Directory delle blacklist. L'interfaccia scopre automaticamente tutti i file `.conf`, `.txt`, `.csv` presenti. |
| `MANUAL_LIST` | `$BL_DIR/manual.txt` | File di testo per i blocchi manuali |
| `WHITELIST` | `$BL_DIR/whitelist.txt` | Domini esclusi da tutti i blocchi |
| `APPLY_CMD` | *(vuoto)* | Comando eseguito dopo ogni modifica alla lista manuale o alla whitelist (es. rigenera `MANUAL.conf` e ricarica Unbound) |
| `UPDATE_CMD` | *(vuoto)* | Comando eseguito al click su "Aggiorna liste" nell'interfaccia |
| `OFFICIAL_LISTS` | `AAMS,ADMT,CNCPO,AGCOM,CONSOB,IVASS` | Nomi delle liste mostrate con il badge "ufficiale" (corrisponde al nome del file `.conf` senza estensione) |
| `UNBOUND_SERVICE` | `unbound` | Nome del servizio per `service <nome> reload` |
| `PORT` | `5000` | Porta HTTP |
| `USERS_FILE` | `<dir_app>/users.json` | File credenziali utenti (generato da `manage_users.py`) |
| `AUDIT_LOG` | `/var/log/dns_gui_audit.log` | Log di tutte le azioni di scrittura e degli accessi |

### Script di aggiornamento (`censorship/update.sh`)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `BL_DIR` | `/etc/unbound/blacklists` | Directory di lavoro per i file scaricati |
| `CONF_DIR` | `/usr/local/etc/unbound/blacklists.d` | Directory di output per i file `.conf` generati |
| `WHITELIST` | `$BL_DIR/whitelist.txt` | Domini da escludere da tutte le liste |
| `CNCPO_FILE` | *(vuoto)* | Percorso del CSV CNCPO. La lista non è scaricabile pubblicamente: viene distribuita agli ISP tramite canale dedicato. |
| `AAMS_URL` | *(vuoto)* | URL della blacklist ADM giochi (lista in formato testo). L'URL corrente va recuperato dal portale [ADM](https://www.adm.gov.it), sezione Giochi → elenco siti non autorizzati. |
| `ADMT_URL` | *(vuoto)* | URL della blacklist ADM tabacchi (lista in formato testo). L'URL corrente va recuperato dal portale [ADM](https://www.adm.gov.it), sezione Tabacchi → elenco siti non autorizzati. |

---

## Whitelist

I domini presenti in `WHITELIST` vengono esclusi da tutte le liste al momento del parsing:

- `update.sh` passa `-w $WHITELIST` a ogni chiamata di `parse.py`
- L'interfaccia web mostra lo stato di whitelist nei risultati di ricerca
- La whitelist si gestisce direttamente dall'interfaccia web

---

## Lista manuale e provvedimenti giudiziari

La lista `MANUAL` è un file di testo (`BL_DIR/manual.txt`) modificabile dall'interfaccia web.
Ogni volta che viene modificata, l'interfaccia esegue `APPLY_CMD`, che deve rigenerare `MANUAL.conf` e ricaricare Unbound.

Esempio di `APPLY_CMD`:

```sh
python3 /opt/it-censura/censorship/parse.py \
    -f plain \
    -i /etc/unbound/blacklists/manual.txt \
    -o /usr/local/etc/unbound/blacklists.d/MANUAL.conf \
  && service unbound reload
```

---

## Aggiornamento automatico

Aggiungere una voce cron per eseguire `update.sh` settimanalmente:

```sh
0 3 * * 0  root \
  BL_DIR=/etc/unbound/blacklists \
  CONF_DIR=/usr/local/etc/unbound/blacklists.d \
  CNCPO_FILE=/etc/unbound/cncpo_list.csv \
  AAMS_URL=https://... \
  ADMT_URL=https://... \
  sh /opt/it-censura/censorship/update.sh \
  && service unbound reload \
  >> /var/log/dns_blacklist_update.log 2>&1
```

In alternativa usare `weekly_update.sh` come wrapper:

```sh
UPDATE_CMD="sh /opt/it-censura/censorship/update.sh" sh weekly_update.sh
```

---

## Liste community (opzionale)

Per chi affianca alle liste ufficiali delle liste community in formato Unbound (es. malware, phishing),
è possibile posizionarle in `BL_DIR` come file `BL_*.conf` ed eseguire `BL_concat.sh` per unire
e deduplicare tutto in un unico file `merged.conf`:

```sh
BL_DIR=/etc/unbound/blacklists \
CONF_DIR=/usr/local/etc/unbound/blacklists.d \
OUT_FILE=/usr/local/etc/unbound/blacklists.d/merged.conf \
sh censorship/BL_concat.sh
```

In questo caso caricare esplicitamente il file unificato in `unbound.conf` invece del wildcard:

```
server:
    include: "/usr/local/etc/unbound/blacklists.d/merged.conf"
```

### Interfaccia web con liste community

Quando si usa `BL_concat.sh`, il file effettivamente caricato da Unbound è `merged.conf` in `CONF_DIR`.
L'interfaccia web deve quindi essere avviata con `BL_DIR` puntato a `CONF_DIR`, altrimenti vedrebbe
i singoli file sorgente invece del file unificato attivo. Poiché `manual.txt` e `whitelist.txt`
rimangono in `BL_DIR`, vanno indicati esplicitamente:

```sh
BL_DIR=/usr/local/etc/unbound/blacklists.d \
MANUAL_LIST=/etc/unbound/blacklists/manual.txt \
WHITELIST=/etc/unbound/blacklists/whitelist.txt \
APPLY_CMD="sh /opt/it-censura/censorship/BL_concat.sh && service unbound reload" \
UPDATE_CMD="sh /opt/it-censura/censorship/update.sh && sh /opt/it-censura/censorship/BL_concat.sh && service unbound reload" \
python3 app.py
```

> **Nota:** con questa configurazione `APPLY_CMD` riesegue `BL_concat.sh` dopo ogni modifica alla
> lista manuale, in modo che `merged.conf` rimanga aggiornato.

---

## Piracy Shield

Piracy Shield è la piattaforma AGCOM per il blocco in tempo reale di siti che violano il diritto d'autore.
Gli ISP accreditati ricevono ticket contenenti FQDN da bloccare via DNS e indirizzi IP da bloccare via BGP blackhole,
con uno SLA di 30 minuti dall'apertura del ticket.

### VPN site-to-site (FreeBSD + StrongSwan)

L'accesso alla piattaforma richiede una VPN IPsec site-to-site tra il router dell'ISP e
l'infrastruttura Azure di AGCOM. La connessione viene configurata una volta sola, in
collaborazione con AGCOM, e rimane attiva in modo permanente.

#### Parametri scambiati con AGCOM

| Parametro | Fornito da | Variabile |
|-----------|-----------|-----------|
| IP pubblico dispositivo VPN ISP | ISP → AGCOM | `VPN_LOCAL_IP` |
| Rete on-premises ISP (CIDR) | ISP → AGCOM | `VPN_LOCAL_NET` |
| IP gateway VPN Azure | AGCOM → ISP | `VPN_AZURE_GW_IP` |
| Rete virtuale Azure (CIDR) | AGCOM → ISP | `VPN_AZURE_NET` |
| Chiave condivisa (PSK) | AGCOM → ISP | `VPN_PSK` |

#### Configurazione e avvio

```sh
VPN_LOCAL_IP=203.0.113.1 \
VPN_LOCAL_NET=192.168.0.0/24 \
VPN_AZURE_GW_IP=20.10.20.30 \
VPN_AZURE_NET=10.0.0.0/16 \
VPN_PSK=chiave_segreta_agcom \
sh vpn/ps_vpn_setup.sh
```

Lo script:
1. Installa StrongSwan via `pkg` se non già presente
2. Scrive `/usr/local/etc/ipsec.conf` e `/usr/local/etc/ipsec.secrets` (`chmod 600`)
3. Aggiunge `strongswan_enable="YES"` a `/etc/rc.conf`
4. Avvia (o riavvia) il servizio

#### Verifica stato VPN

```sh
sh vpn/ps_vpn_check.sh
```

Controlla che il servizio sia attivo, che il tunnel risulti `ESTABLISHED` e che
l'host API sia raggiungibile. In caso di problemi stampa suggerimenti diagnostici.

#### Avvio automatico al boot

Il flag `strongswan_enable="YES"` in `/etc/rc.conf` garantisce il riavvio automatico
del tunnel ad ogni reboot del sistema.

Lo script `censorship/ps_sync.py` si integra nella stessa architettura degli altri script: nessun database,
nessuna VM, solo file di testo e variabili d'ambiente.

### Prerequisiti

- Accreditamento AGCOM completato (credenziali email + password ricevute via PEC)
- Connessione VPN site-to-site attiva verso l'infrastruttura Azure di Piracy Shield
- Per il BGP blackhole: OpenBGPD installato e configurato con un peer iBGP

### Cosa produce

| File | Contenuto | Utilizzato da |
|------|-----------|---------------|
| `PS.conf` | Domini in formato `always_nxdomain` | Unbound (scoperto automaticamente dall'interfaccia web) |
| `ps_ipv4.txt` | Un IPv4 per riga | `ps_bgp_push.sh` / BGP daemon |
| `ps_ipv6.txt` | Un IPv6 per riga | `ps_bgp_push.sh` / BGP daemon |

### Esecuzione manuale

```sh
PS_URL=https://psp01.agcom.it/api \
PS_EMAIL=utente@isp.it \
PS_PASSWORD=password_sicura \
PS_FQDN_CONF=/usr/local/etc/unbound/blacklists.d/PS.conf \
PS_IPV4_FILE=/etc/unbound/blacklists/ps_ipv4.txt \
PS_IPV6_FILE=/etc/unbound/blacklists/ps_ipv6.txt \
python3 censorship/ps_sync.py
```

Dopo la sincronizzazione DNS, ricaricare Unbound e aggiornare il BGP:

```sh
service unbound reload
sh censorship/ps_bgp_push.sh
```

### Variabili di configurazione

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `PS_URL` | *(obbligatoria)* | URL base API (`https://psp01.agcom.it/api` in produzione, `https://psp01-dev.agcom.it/api` in test) |
| `PS_EMAIL` | *(obbligatoria)* | Email di accesso alla piattaforma |
| `PS_PASSWORD` | *(obbligatoria)* | Password di accesso |
| `PS_TOKEN_FILE` | `~/.ps_tokens.json` | File dove salvare i token JWT tra un'esecuzione e l'altra (`chmod 600`) |
| `PS_STATE_FILE` | `~/.ps_state.json` | File dove salvare gli item già processati per rilevare i duplicati (`chmod 600`) |
| `PS_FQDN_CONF` | `/usr/local/etc/unbound/blacklists.d/PS.conf` | Output Unbound per i domini |
| `PS_IPV4_FILE` | `/etc/unbound/blacklists/ps_ipv4.txt` | Output IPv4 per BGP blackhole |
| `PS_IPV6_FILE` | `/etc/unbound/blacklists/ps_ipv6.txt` | Output IPv6 per BGP blackhole |
| `PS_MARK_PROCESSED` | `true` | Se inviare il feedback di stato all'API dopo ogni sincronizzazione |

### Gestione automatica dei token

Lo script gestisce autonomamente il ciclo di vita dei token JWT:
- **Access token** (durata 1h): rinnovato automaticamente tramite il refresh token
- **Refresh token** (durata 7gg): alla scadenza viene eseguito un nuovo login con le credenziali
- I token sono salvati in `PS_TOKEN_FILE` con permessi `600`; il login avviene solo quando necessario

### Logica di stato (processed / ALREADY_BLOCKED)

Per ogni ticket degli ultimi 48 ore lo script invia ad AGCOM il feedback di elaborazione:
- **`processed`** — item mai visto in precedenza: blocco applicato per la prima volta
- **`ALREADY_BLOCKED`** — item già presente in un ticket precedente: era già bloccato

Lo stato locale è mantenuto in `PS_STATE_FILE` (set di tutti gli FQDN e IP mai processati).

### Aggiornamento automatico

Aggiungere una voce cron per eseguire la sincronizzazione ogni 20 minuti
(il ticket ha una finestra SLA di 30 minuti):

```sh
*/20 * * * *  root \
  PS_URL=https://psp01.agcom.it/api \
  PS_EMAIL=utente@isp.it \
  PS_PASSWORD=password_sicura \
  PS_FQDN_CONF=/usr/local/etc/unbound/blacklists.d/PS.conf \
  PS_IPV4_FILE=/etc/unbound/blacklists/ps_ipv4.txt \
  PS_IPV6_FILE=/etc/unbound/blacklists/ps_ipv6.txt \
  python3 /opt/it-censura/censorship/ps_sync.py \
  && service unbound reload \
  && sh /opt/it-censura/censorship/ps_bgp_push.sh \
  >> /var/log/ps_sync.log 2>&1
```

### BGP blackhole con OpenBGPD

Lo script `ps_bgp_push.sh` usa `bgpctl` per caricare gli IP in OpenBGPD:

```sh
PS_IPV4_FILE=/etc/unbound/blacklists/ps_ipv4.txt \
PS_IPV6_FILE=/etc/unbound/blacklists/ps_ipv6.txt \
sh censorship/ps_bgp_push.sh
```

Lo script esegue `bgpctl network flush` prima di ricaricare, così rimuove automaticamente
gli IP che AGCOM ha eliminato da ticket precedenti.
Per daemon BGP diversi da OpenBGPD (Bird, FRR, ExaBGP) è sufficiente adattare
`ps_bgp_push.sh` alla sintassi del proprio daemon, usando `ps_ipv4.txt` e `ps_ipv6.txt`
come sorgente.

---

## Gestione utenti

```sh
python3 manage_users.py add     <utente>   # aggiunge utente (chiede la password)
python3 manage_users.py passwd  <utente>   # cambia password
python3 manage_users.py list               # elenca tutti gli utenti
python3 manage_users.py enable  <utente>   # riabilita un utente disabilitato
python3 manage_users.py disable <utente>   # disabilita il login senza eliminare
python3 manage_users.py delete  <utente>   # elimina l'utente
```

Le credenziali sono salvate in `users.json` con password hashate (`chmod 600`).

---

## Riferimento parse.py

`censorship/parse.py` converte le blacklist ufficiali nel formato `always_nxdomain` di Unbound.

```
utilizzo: parse.py -f FORMATO -i INPUT -o OUTPUT [-w WHITELIST]

  -f plain   Testo semplice: un dominio per riga (dominio, formato hosts, o URL)
  -f cncpo   CSV separato da punto e virgola: dominio in colonna 2, prima riga intestazione

  -i FILE    File di input
  -o FILE    File .conf di output
  -w FILE    File whitelist (opzionale)
```

Esempio:

```sh
python3 censorship/parse.py \
    -f cncpo \
    -i /percorso/lista_cncpo.csv \
    -o /usr/local/etc/unbound/blacklists.d/CNCPO.conf \
    -w /etc/unbound/blacklists/whitelist.txt
```

---

## API

Tutti gli endpoint richiedono autenticazione.

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/search?domain=x.com` | Cerca un dominio in tutte le blacklist |
| `GET` | `/api/manual` | Elenca i domini in lista manuale |
| `POST` | `/api/manual` | Aggiunge `{"domain": "x.com"}` alla lista manuale |
| `DELETE` | `/api/manual/<domain>` | Rimuove un dominio dalla lista manuale |
| `GET` | `/api/whitelist` | Elenca i domini in whitelist |
| `POST` | `/api/whitelist` | Aggiunge `{"domain": "x.com"}` alla whitelist |
| `DELETE` | `/api/whitelist/<domain>` | Rimuove un dominio dalla whitelist |
| `GET` | `/api/stats` | Numero di voci per lista (cache 5 minuti) |
| `POST` | `/api/reload` | Ricarica il servizio Unbound |
| `POST` | `/api/update` | Avvia l'aggiornamento completo delle liste (output in streaming) |
| `GET` | `/api/update/status` | Verifica lo stato dell'aggiornamento in corso |
