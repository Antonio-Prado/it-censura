#!/usr/bin/env python3
"""
Scarica e normalizza la lista dei siti abusivi dall'IVASS.

Il sito pubblica un indice paginato di comunicati stampa; ogni voce è un PDF
che contiene i domini da bloccare come hyperlink e/o testo.  Lo script:
  1. Recupera tutte le pagine dell'indice
  2. Raccoglie i link ai PDF dei provvedimenti (pattern ivcs*.pdf)
  3. Per ciascun PDF estrae domini via testo (pdfplumber + URLExtract)
     e via annotazioni ipertestuali
  4. Filtra i domini interni IVASS e normalizza con tldextract
  5. Scrive un dominio per riga nel file di output
"""

import io
import optparse
import re
import sys

import pypdf
import requests
from bs4 import BeautifulSoup
from tldextract import extract as tldext
from urlextract import URLExtract
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

BASE_URL  = "https://www.ivass.it"
INDEX_URL = BASE_URL + "/cyber/siti-abusivi/index.html"

# Domini da escludere sempre (IVASS stesso, enti istituzionali)
EXCLUDE_DOMAINS = {
    "ivass.it",
    "governo.it",
    "giustizia.it",
    "example.com",
}


def get_all_pdf_urls():
    """Itera le pagine dell'indice e restituisce tutti gli URL dei PDF provvedimenti."""
    pdf_urls = []
    seen = set()
    page_num = 1

    while True:
        url = f"{INDEX_URL}?page={page_num}" if page_num > 1 else INDEX_URL
        try:
            resp = requests.get(url, verify=False, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"Warning: impossibile recuperare pagina {page_num}: {e}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        found_on_page = 0

        for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = a["href"].strip()
            if href.startswith("/"):
                href = BASE_URL + href
            # Considera solo i comunicati IVASS (pattern ivcs*.pdf)
            if "ivcs" not in href.lower():
                continue
            if href not in seen:
                seen.add(href)
                pdf_urls.append(href)
                found_on_page += 1

        if found_on_page == 0:
            break

        # Passa alla pagina successiva se esiste il link
        if not soup.find("a", href=re.compile(rf"[?&]page={page_num + 1}")):
            break
        page_num += 1

    return pdf_urls


def normalize_domain(raw_url):
    """
    Ricava il dominio registrato da un URL/dominio grezzo.
    Restituisce None se non è un dominio valido o va escluso.
    """
    tsd, td, tsu = tldext(raw_url)
    if not td or not tsu:
        return None
    full = f"{tsd}.{td}.{tsu}" if tsd else f"{td}.{tsu}"
    if f"{td}.{tsu}" in EXCLUDE_DOMAINS:
        return None
    # Scarta indirizzi IP, localhost e token generici
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", td):
        return None
    return full.lower().strip(".")


def extract_domains_from_pdf(pdf_bytes):
    """Estrae domini da un PDF tramite testo e annotazioni ipertestuali."""
    extractor = URLExtract()
    domains = set()

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            # 1. Testo visibile
            text = page.extract_text() or ""
            for url in extractor.find_urls(text):
                d = normalize_domain(url)
                if d:
                    domains.add(d)

            # 2. Annotazioni hyperlink (URI embedded nel PDF)
            if "/Annots" in page:
                for annot_ref in page["/Annots"]:
                    try:
                        annot = annot_ref.get_object()
                        if annot.get("/Subtype") == "/Link":
                            action = annot.get("/A")
                            if action and action.get("/S") == "/URI":
                                uri = str(action["/URI"])
                                d = normalize_domain(uri)
                                if d:
                                    domains.add(d)
                    except Exception:
                        pass
    except Exception as e:
        print(f"Warning: errore nel parsing del PDF: {e}", file=sys.stderr)

    return domains


def main():
    usage = "usage: %prog -o <output_file>"
    parser = optparse.OptionParser(usage)
    parser.add_option("-o", "--output", dest="out_file", help="File di output (un dominio per riga)")

    (options, args) = parser.parse_args()
    if options.out_file is None:
        parser.error("Specificare il file di output con -o")

    all_domains = set()
    pdf_urls = get_all_pdf_urls()
    print(f"Trovati {len(pdf_urls)} PDF da processare", file=sys.stderr)

    for pdf_url in pdf_urls:
        try:
            resp = requests.get(pdf_url, verify=False, timeout=60)
            resp.raise_for_status()
            domains = extract_domains_from_pdf(resp.content)
            all_domains.update(domains)
        except Exception as e:
            print(f"Warning: impossibile processare {pdf_url}: {e}", file=sys.stderr)

    print(f"Domini totali estratti: {len(all_domains)}", file=sys.stderr)

    with open(options.out_file, "wb") as f:
        f.write(("\n".join(sorted(all_domains)) + "\n").encode())


if __name__ == "__main__":
    main()
