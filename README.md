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
| `AAMS_URL` | *(vuoto)* | URL della blacklist ADM giochi (lista in formato testo). Pubblicata dall'[Agenzia delle Dogane e dei Monopoli](https://www.adm.gov.it). |
| `ADMT_URL` | *(vuoto)* | URL della blacklist ADM tabacchi (lista in formato testo). Stessa fonte. |

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
e deduplicare tutto in un unico file `BL_all.conf`:

```sh
BL_DIR=/etc/unbound/blacklists \
CONF_DIR=/usr/local/etc/unbound/blacklists.d \
sh censorship/BL_concat.sh
```

In questo caso caricare esplicitamente `BL_all.conf` in `unbound.conf` invece del wildcard:

```
server:
    include: "/usr/local/etc/unbound/blacklists.d/BL_all.conf"
```

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
