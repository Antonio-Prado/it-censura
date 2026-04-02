#!/usr/bin/env python3
"""
Scarica l'ultima blacklist AGCOM a tutela del diritto d'autore (Allegato B).

Analizza il sito AGCOM per trovare il "Provvedimento" più recente contenente
una "Determina" con allegato "Allegato B" e lo scarica.
L'output è tipicamente una lista di domini in testo semplice compatibile con parse.py --format plain.
"""

import argparse
import sys

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

BASE_URL  = "https://www.agcom.it"
INDEX_URL = BASE_URL + "/provvedimenti-a-tutela-del-diritto-d-autore"
MAX_PAGES = 50
TIMEOUT   = 30


def find_allegato_b() -> str | None:
    """Restituisce l'URL dell'Allegato B più recente, o None se non trovato."""
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{INDEX_URL}?p_p_id=listapersconform_WAR_agcomlistsportlet"
            f"&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
            f"&p_p_col_id=column-1&p_p_col_count=1"
            f"&_listapersconform_WAR_agcomlistsportlet_numpagris=50"
            f"&_listapersconform_WAR_agcomlistsportlet_curpagris={page}"
        )
        try:
            resp = requests.get(url, verify=False, timeout=TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            print(f"Attenzione: pagina {page} non scaricabile: {e}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        for div in soup.find_all("div", class_="risultato"):
            for p in div.find_all("p"):
                text = p.text.lower()
                if "provvedimento" not in text and "ordine" not in text:
                    continue
                determina = div.find(
                    lambda tag: tag.name == "a" and "determina" in tag.text.lower()
                )
                if not determina:
                    continue
                det_url = BASE_URL + determina["href"]
                try:
                    det_resp = requests.get(det_url, verify=False, timeout=TIMEOUT)
                    det_resp.raise_for_status()
                except Exception as e:
                    print(f"Attenzione: determina non scaricabile: {e}", file=sys.stderr)
                    continue
                det_soup = BeautifulSoup(det_resp.content, "html.parser")
                for a in det_soup.find_all("a"):
                    if "Allegato B" in a.text:
                        return a["href"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scarica l'ultima blacklist AGCOM sul diritto d'autore (Allegato B)."
    )
    parser.add_argument("-o", "--output", required=True, help="Percorso del file di output")
    args = parser.parse_args()

    allegato_url = find_allegato_b()
    if not allegato_url:
        print("Errore: Allegato B non trovato sul sito AGCOM.", file=sys.stderr)
        sys.exit(1)

    try:
        resp = requests.get(allegato_url, verify=False, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"Errore durante il download dell'Allegato B: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "wb") as f:
        f.write(resp.content)
    print(f"AGCOM Allegato B → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
