#!/usr/bin/env python3
"""
Scarica la lista CONSOB dei siti finanziari oscurati.

Scorre le pagine pubbliche degli "oscuramenti" CONSOB, estrae i nomi di dominio
dai blocchi di testo libero che descrivono ogni ordine di oscuramento, e scrive
un dominio per riga nel file di output.
"""

import argparse
import re
import sys

import requests
from bs4 import BeautifulSoup
from tldextract import extract as tldext
from urlextract import URLExtract
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

BASE_URL  = "https://www.consob.it"
INDEX_TPL = (
    BASE_URL
    + "/web/area-pubblica/oscuramenti"
    "?p_p_id=com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet_INSTANCE_m9PTOY4SM1GU"
    "&_com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet_INSTANCE_m9PTOY4SM1GU_cur={page}"
)
TIMEOUT = 30


def _normalize(raw: str) -> str | None:
    tsd, td, tsu = tldext(raw)
    if not td or not tsu:
        return None
    return f"{tsd}.{td}.{tsu}" if tsd else f"{td}.{tsu}"


def _total_pages(soup: BeautifulSoup) -> int:
    for span in soup.find_all("span", class_="lfr-icon-menu-text"):
        m = re.search(r"(\d+)$", span.get_text())
        if m:
            return int(m.group(1))
    return 1


def scrape() -> list[str]:
    extractor = URLExtract()
    seen: dict[str, None] = {}

    try:
        resp = requests.get(INDEX_TPL.format(page=1), verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"Errore nel recupero dell'indice CONSOB: {e}", file=sys.stderr)
        sys.exit(1)

    soup  = BeautifulSoup(resp.content, "html.parser")
    total = _total_pages(soup)

    for page in range(1, total + 1):
        if page > 1:
            try:
                resp = requests.get(INDEX_TPL.format(page=page), verify=False, timeout=TIMEOUT)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.content, "html.parser")
            except Exception as e:
                print(f"Attenzione: pagina {page} non scaricabile: {e}", file=sys.stderr)
                continue

        for div in soup.find_all("div", class_="divContent"):
            for node in div.find_all(string=re.compile(r"Di seguito.*siti|riguardano i siti")):
                block = node.find_next().get_text()
                for url in extractor.find_urls(block):
                    d = _normalize(url)
                    if d:
                        seen[d] = None

    return list(seen)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scarica la lista CONSOB dei siti finanziari oscurati."
    )
    parser.add_argument("-o", "--output", required=True, help="Percorso del file di output")
    args = parser.parse_args()

    domains = scrape()
    with open(args.output, "w") as f:
        f.write("\n".join(domains) + "\n")
    print(f"CONSOB: {len(domains)} domini → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
