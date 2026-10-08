#!/usr/bin/env python3
"""
Scarica l'elenco AGCOM dei siti oggetto di disabilitazione (diritto d'autore).

Storia: fino a giugno 2024 AGCOM pubblicava un elenco cumulativo "Allegato B" in formato .txt
allegato all'ultima determina DDA. Con il nuovo sito (Drupal) e il nuovo regolamento, ogni
provvedimento (determina DDA o delibera CSP) e' un PDF e il/i nome/i a dominio compaiono solo
nella parte dispositiva ("DISPONE" / "ORDINA" / "ORDERS").

Strategia:
  1. elenco base = ultimo Allegato B .txt pubblicato (giugno 2024), con copia locale di riserva;
  2. enumera dall'archivio provvedimenti tutte le determine DDA e le delibere CSP del settore
     "Piattaforme online" pubblicate dopo quella data;
  3. per ciascun provvedimento nuovo scarica il PDF, estrae il testo (pypdf) e i domini citati
     nella parte dispositiva; il risultato e' memorizzato in una cache JSON accanto al file di
     output, cosi' i giri successivi elaborano solo i provvedimenti nuovi;
  4. scrive l'unione (base + cache) nel file di output, un dominio per riga.

Uso: download_agcom.py -o <file_output> [--max N] [--rescan] [--verbose]

La cache dei provvedimenti elaborati (agcom_state.json) viene scritta nella directory del file di output.
"""

import json
import argparse
import os
import re
import sys
import time
from io import BytesIO

import requests
from urllib3.exceptions import InsecureRequestWarning

try:
    import tldextract
    # suffix_list_urls vuoto = usa solo lo snapshot incluso nel pacchetto, nessuna richiesta di rete
    _tld = tldextract.TLDExtract(suffix_list_urls=())
except Exception:   # pragma: no cover - tldextract assente: si usa solo la regex
    _tld = None

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

SITE = "https://www.agcom.it"
BASELINE_URL = SITE + "/sites/default/files/media/allegato/2024/Allegato%20B_11.txt"
BASELINE_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agcom_baseline_2024-06.txt")
SINCE = "2024-06-07"      # giorno dopo l'ultimo Allegato B cumulativo (0000311_2024.06.06)
UA = "Mozilla/5.0 (compatible; dns-blacklist-updater/2.0)"
TIMEOUT = 60
PAUSE = 0.3               # secondi tra una richiesta e l'altra

# Elenchi dell'archivio provvedimenti da scandire (query string, senza page=)
LISTINGS = [
    ("determina DDA", "type=determina&title_1=DDA"),
    ("delibera CSP piattaforme", "type=delibera&title_1=CSP&field_settore_competenza_target_id=10"),
]
DATE_FILTER = "data_pubblicazione%5Bmin%5D%5Bdate%5D=" + SINCE

CARD_RE = re.compile(r'class="card-title h5"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
PDF_RE = re.compile(r'href="(/sites/default/files/[^"]+\.pdf)"', re.I)
OPERATIVE_RE = re.compile(r'\n\s*(DISPONE|ORDINA|ORDERS|DELIBERA|DETERMINA)\s*\n')
URL_RE = re.compile(r'https?://[^\s,;"\'<>()\[\]]+', re.I)
BARE_DOMAIN_RE = re.compile(
    r'(?<![\w.@/-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])', re.I)
DOMAIN_OK_RE = re.compile(r'^(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$')
# host da non bloccare mai (compaiono nei provvedimenti come riferimenti normativi/istituzionali)
SKIP_HOSTS = ("agcom.it", "normattiva.it", "eur-lex.europa.eu", "europa.eu", "gazzettaufficiale.it",
              "example.com", "example.org", "siae.it", "fapav.it", "dgca.it", "google.com",
              "interno.gov.it", "giustizia.it", "pec.it", "legalmail.it")

session = requests.Session()
session.headers["User-Agent"] = UA
VERBOSE = False


def log(msg):
    print(msg, file=sys.stderr)
    sys.stderr.flush()


def get(url, binary=False):
    """GET con retry; TLS verificato, con fallback non verificato (il vecchio script usava verify=False)."""
    last = None
    for attempt in range(3):
        for verify in (True, False):
            try:
                r = session.get(url, timeout=TIMEOUT, verify=verify)
                if r.status_code == 200:
                    return r.content if binary else r.text
                last = "HTTP %s" % r.status_code
                if r.status_code == 404:
                    raise RuntimeError(last)
            except requests.exceptions.SSLError as exc:
                last = "SSL: %s" % exc
                continue
            except requests.exceptions.RequestException as exc:
                last = str(exc)
            break
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("download fallito %s: %s" % (url, last))


def _valid_suffix(d):
    """True se il TLD/suffisso pubblico esiste (filtra artefatti dell'estrazione PDF
    come 'libeccio.this' o 'annas-archive.gderrore')."""
    if _tld is None:
        return True
    return bool(_tld(d).suffix)


def normalise_domain(raw):
    d = raw.strip().lower().rstrip(".").strip("/")
    d = re.sub(r"^(https?://)+", "", d)      # anche "https://https://sito.tld" (refuso AGCOM)
    d = d.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    if d.startswith("www."):
        d = d[4:]
    if not DOMAIN_OK_RE.match(d):
        return None
    if not _valid_suffix(d):
        # artefatto tipico dei PDF generati da Word: "dominio.gdErrore. L'origine riferimento..."
        d2 = re.sub(r"errore$", "", d)
        if d2 == d or not DOMAIN_OK_RE.match(d2) or not _valid_suffix(d2):
            return None
        d = d2
    if any(d == h or d.endswith("." + h) for h in SKIP_HOSTS):
        return None
    return d


def load_baseline():
    """Allegato B cumulativo di giugno 2024: prova il download, altrimenti la copia locale."""
    text = None
    try:
        text = get(BASELINE_URL, binary=True).decode("utf-8", "replace")
        if "0000311_2024.06.06" not in text[:300]:
            log("ATTENZIONE: Allegato B remoto inatteso, uso la copia locale")
            text = None
    except Exception as exc:
        log("Allegato B remoto non scaricabile (%s), uso la copia locale" % exc)
    if text is None:
        with open(BASELINE_LOCAL, encoding="utf-8", errors="replace") as f:
            text = f.read()
    domains = set()
    for line in text.splitlines():
        d = normalise_domain(line)
        if d:
            domains.add(d)
    return domains


def enumerate_listing(query, known, rescan):
    """Restituisce [(href, titolo)] dei provvedimenti dell'elenco, piu' recenti per primi.
    Senza --rescan si ferma alla prima pagina composta solo da provvedimenti gia' in cache."""
    items = []
    for page in range(0, 200):
        url = "%s/provvedimenti?%s&%s&page=%d" % (SITE, query, DATE_FILTER, page)
        html = get(url)
        found = CARD_RE.findall(html)
        if not found:
            break
        new_on_page = 0
        for href, title in found:
            title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", title)).strip()
            if href in [i[0] for i in items]:
                continue
            items.append((href, title))
            if href not in known:
                new_on_page += 1
        if not rescan and known and new_on_page == 0:
            break
        time.sleep(PAUSE)
    return items


def extract_pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def domains_from_text(text):
    """Domini citati nella parte dispositiva (dopo DISPONE/ORDINA/ORDERS/DELIBERA).
    Se la parte dispositiva non si trova, non estrae nulla (meglio un falso negativo)."""
    m = OPERATIVE_RE.search(text)
    if not m:
        return set(), "parte dispositiva non trovata"
    op = text[m.end():]
    found = set()
    # PDF generati da Word con il campo del dominio rotto ("http://Errore. L'origine riferimento
    # non e' stata trovata."): il dominio sparisce dal dispositivo, ma nelle premesse resta
    # ("...modifica del nome a dominio del sito X nel nuovo https://Y").
    if re.search(r"https?://Errore\.", op):
        # 1) intestazione: "DDA/14770 – http://casacinema.green"
        head = text[:2000]
        for mm in re.finditer(r"DDA/\d+\s*[–—-]\s*(https?://[^\s,;\"'<>()]+|(?:[a-z0-9-]+\.)+[a-z]{2,24})", head, re.I):
            d = normalise_domain(mm.group(1).rstrip(".,;:"))
            if d:
                found.add(d)
        # 2) premesse: "...nel nuovo https://sito.tld"
        for mm in re.finditer(r"nel\s+nuovo\s+(?:nome\s+a\s+dominio\s+)?(https?://[^\s,;\"'<>()]+)", text, re.I):
            d = normalise_domain(mm.group(1).rstrip(".,;:"))
            if d:
                found.add(d)
    for u in URL_RE.findall(op):
        d = normalise_domain(u.rstrip(".,;:"))
        if d:
            found.add(d)
    # domini scritti senza schema (es. "sito pirata.example") subito dopo parole chiave
    for mm in re.finditer(r'(?:sito|siti|website|websites|dominio|domain|domini|domains)\s+(?:internet\s+)?'
                          r'(?:denominat[oi]\s+)?["“]?((?:[a-z0-9-]+\.)+[a-z]{2,24})', op, re.I):
        d = normalise_domain(mm.group(1))
        if d:
            found.add(d)
    return found, m.group(1)


def process_item(href):
    """Scarica pagina e PDF del provvedimento, ritorna dict per la cache."""
    page = get(SITE + href)
    pdfs = PDF_RE.findall(page)
    if not pdfs:
        return {"pdf": None, "domains": [], "note": "nessun PDF nella pagina"}
    pdf_url = SITE + pdfs[0]
    data = get(pdf_url, binary=True)
    if not data.startswith(b"%PDF"):
        return {"pdf": pdf_url, "domains": [], "note": "il link non e' un PDF"}
    text = extract_pdf_text(data)
    domains, note = domains_from_text(text)
    lower = text.lower()
    flags = [k for k in ("revoca", "annullament", "archiviazione", "rigett") if k in lower[:6000]]
    return {"pdf": pdf_url, "domains": sorted(domains), "note": note,
            "flags": flags, "text_len": len(text)}


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="Scarica l'elenco AGCOM dei siti oggetto di disabilitazione")
    parser.add_argument("-o", "--output", dest="out_file", required=True, help="File di output generato")
    parser.add_argument("--max", dest="max_items", type=int, default=0,
                        help="Elabora al massimo N provvedimenti nuovi (test)")
    parser.add_argument("--rescan", dest="rescan", action="store_true", default=False,
                        help="Riscandisce tutte le pagine degli elenchi, non solo le prime")
    parser.add_argument("--verbose", dest="verbose", action="store_true", default=False)
    options = parser.parse_args()
    VERBOSE = options.verbose

    state_path = os.path.join(os.path.dirname(os.path.abspath(options.out_file)), "agcom_state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError) as exc:
            log("Cache %s illeggibile (%s): riparto da zero" % (state_path, exc))
            state = {}
    items_state = state.setdefault("items", {})

    baseline = load_baseline()
    log("AGCOM: elenco base giugno 2024: %d domini" % len(baseline))

    todo = []
    for name, query in LISTINGS:
        listing = enumerate_listing(query, items_state, options.rescan)
        new = [(h, t) for h, t in listing if h not in items_state]
        log("AGCOM: %s dal %s: %d provvedimenti, %d nuovi" % (name, SINCE, len(listing), len(new)))
        todo.extend(new)

    if options.max_items:
        todo = todo[:options.max_items]

    errors = 0
    for n, (href, title) in enumerate(todo, 1):
        try:
            info = process_item(href)
            info["title"] = title
            info["processed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            items_state[href] = info
            if VERBOSE or info["domains"] or info.get("flags"):
                log("  [%d/%d] %s -> %s %s" % (n, len(todo), title, info["domains"] or "-",
                                              ("flag=" + ",".join(info["flags"])) if info.get("flags") else ""))
        except Exception as exc:
            errors += 1
            log("  [%d/%d] %s ERRORE: %s" % (n, len(todo), title, exc))
        # salva la cache a ogni passo: un'interruzione non perde il lavoro fatto
        if n % 10 == 0 or n == len(todo):
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, state_path)
        time.sleep(PAUSE)

    extra = set()
    for info in items_state.values():
        extra.update(info.get("domains", []))
    all_domains = sorted(baseline | extra)
    tmp = options.out_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for d in all_domains:
            f.write(d + "\n")
    os.replace(tmp, options.out_file)
    log("AGCOM: scritti %d domini (%d base + %d da %d provvedimenti); errori: %d"
        % (len(all_domains), len(baseline), len(extra), len(items_state), errors))
    if errors and not items_state:
        sys.exit(1)


if __name__ == "__main__":
    main()
