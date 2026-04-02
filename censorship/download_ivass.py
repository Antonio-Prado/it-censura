#!/usr/bin/env python3
"""
Scarica la lista IVASS dei siti assicurativi/broker abusivi.

Scorre l'indice "siti abusivi" IVASS, raccoglie i link ai PDF (ivcs*.pdf),
estrae i domini da ciascun PDF tramite testo visibile e annotazioni di collegamento,
e scrive un dominio per riga nel file di output.
"""

import argparse
import io
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
TIMEOUT   = 30

EXCLUDE = {"ivass.it", "governo.it", "giustizia.it", "example.com"}


def _normalize(raw: str) -> str | None:
    tsd, td, tsu = tldext(raw)
    if not td or not tsu:
        return None
    full = f"{tsd}.{td}.{tsu}" if tsd else f"{td}.{tsu}"
    if f"{td}.{tsu}" in EXCLUDE:
        return None
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", td):
        return None
    return full.lower().strip(".")


def get_pdf_urls() -> list[str]:
    """Restituisce tutti gli URL ivcs*.pdf dall'indice IVASS (tutte le pagine)."""
    urls: list[str] = []
    seen: set[str] = set()
    page = 1

    while True:
        url = f"{INDEX_URL}?page={page}" if page > 1 else INDEX_URL
        try:
            resp = requests.get(url, verify=False, timeout=TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            print(f"Attenzione: pagina indice {page} non scaricabile: {e}", file=sys.stderr)
            break

        soup  = BeautifulSoup(resp.content, "html.parser")
        found = 0
        for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = a["href"].strip()
            if href.startswith("/"):
                href = BASE_URL + href
            if "ivcs" not in href.lower() or href in seen:
                continue
            seen.add(href)
            urls.append(href)
            found += 1

        if not found:
            break
        if not soup.find("a", href=re.compile(rf"[?&]page={page + 1}")):
            break
        page += 1

    return urls


def extract_from_pdf(pdf_bytes: bytes) -> set[str]:
    extractor = URLExtract()
    domains: set[str] = set()
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        for pg in reader.pages:
            for url in extractor.find_urls(pg.extract_text() or ""):
                d = _normalize(url)
                if d:
                    domains.add(d)
            if "/Annots" in pg:
                for ref in pg["/Annots"]:
                    try:
                        annot = ref.get_object()
                        if annot.get("/Subtype") == "/Link":
                            action = annot.get("/A")
                            if action and action.get("/S") == "/URI":
                                d = _normalize(str(action["/URI"]))
                                if d:
                                    domains.add(d)
                    except Exception:
                        pass
    except Exception as e:
        print(f"Attenzione: errore nel parsing del PDF: {e}", file=sys.stderr)
    return domains


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scarica la lista IVASS dei siti assicurativi/broker abusivi."
    )
    parser.add_argument("-o", "--output", required=True, help="Percorso del file di output")
    args = parser.parse_args()

    pdf_urls = get_pdf_urls()
    print(f"Trovati {len(pdf_urls)} PDF da elaborare", file=sys.stderr)

    all_domains: set[str] = set()
    for pdf_url in pdf_urls:
        try:
            resp = requests.get(pdf_url, verify=False, timeout=60)
            resp.raise_for_status()
            all_domains.update(extract_from_pdf(resp.content))
        except Exception as e:
            print(f"Attenzione: {pdf_url}: {e}", file=sys.stderr)

    with open(args.output, "w") as f:
        f.write("\n".join(sorted(all_domains)) + "\n")
    print(f"IVASS: {len(all_domains)} domini → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
