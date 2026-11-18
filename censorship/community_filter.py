#!/usr/bin/env python3
"""Filtra una lista community di domini e genera il file BL_<nome>.conf per Unbound.

Uso: community_filter.py NOME FORMATO FILE_SCARICATO [--min N] [--top N]
  NOME     nome breve della fonte (es. threatfox): produce $BL_DIR/BL_NOME.conf
  FORMATO  hosts   righe "IP dominio" (hosts file)
           domains un dominio per riga
  --min N  numero minimo di domini validi: sotto questa soglia si tiene la lista precedente
  --top N  esclude i primi N domini della classifica Tranco (default 100000; 0 = nessuna esclusione)

Scarta: righe di commento, IP, nomi malformati, .gov.it, i domini che sono esattamente un
suffisso pubblico (Public Suffix List: es. net.ru, storage.yandexcloud.net) e i domini fra i
primi N di Tranco (siti popolari: possibili falsi positivi). Le esclusioni finiscono in
$BL_DIR/NOME.excluded.log. PSL e Tranco sono scaricati al massimo una volta a settimana in
$BL_DIR/ref/; se il download fallisce si usa la copia precedente.

Variabili d'ambiente:
  BL_DIR   directory dei file community (default: /etc/unbound/blacklists)

In caso di errore il file BL_NOME.conf precedente resta intatto (exit 1).
"""
import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

TMP = Path(os.environ.get("BL_DIR", "/etc/unbound/blacklists"))
REF = TMP / "ref"
MAX_AGE = 7 * 86400
PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
VALID = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
IPV4 = re.compile(r"^[0-9.]+$")


def log(msg):
    print("%s: %s" % (NAME, msg), file=sys.stderr)


def fresh(path):
    return path.exists() and path.stat().st_size > 0 and time.time() - path.stat().st_mtime < MAX_AGE


def fetch_ref(path, url, parse):
    """Scarica un riferimento se manca o e' vecchio; in caso di errore tiene la copia esistente.
    Ritorna la lista delle voci nell'ordine del file (per Tranco: ordine di classifica)."""
    REF.mkdir(parents=True, exist_ok=True)
    if not fresh(path):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            data = parse(r.content)
            if len(data) < 1000:
                raise ValueError("solo %d voci" % len(data))
            tmp = path.with_suffix(".new")
            tmp.write_text("\n".join(data) + "\n")
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001 - qualunque errore: si usa la cache
            log("ATTENZIONE: aggiornamento di %s fallito (%s), uso la copia precedente" % (path.name, e))
    if not path.exists():
        raise RuntimeError("riferimento %s non disponibile" % path.name)
    return path.read_text().split()


def parse_psl(content):
    out = set()
    for line in content.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        out.add(line.lstrip("*.!").lower())
    return sorted(out)


def parse_tranco(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            return [l.decode().strip().split(",", 1)[1].lower()
                    for l in f if l.strip()]


def main():
    global NAME
    args = [a for a in sys.argv[1:]]
    opts = {"--min": 100, "--top": 100000}
    pos = []
    i = 0
    while i < len(args):
        if args[i] in opts:
            opts[args[i]] = int(args[i + 1])
            i += 2
        else:
            pos.append(args[i])
            i += 1
    if len(pos) != 3 or pos[1] not in ("hosts", "domains"):
        print(__doc__, file=sys.stderr)
        return 2
    NAME, fmt, infile = pos
    conf = TMP / ("BL_%s.conf" % NAME)

    raw = Path(infile).read_text(errors="replace").splitlines()
    domains = set()
    for line in raw:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        d = parts[1] if fmt == "hosts" and len(parts) >= 2 else parts[0]
        d = d.lower().rstrip(".")
        if IPV4.match(d) or not VALID.match(d) or d.endswith(".gov.it") or d == "localhost":
            continue
        domains.add(d)

    psl = set(fetch_ref(REF / "psl.txt", PSL_URL, parse_psl))
    top = set()
    if opts["--top"] > 0:
        top = set(fetch_ref(REF / "tranco_top1m.txt", TRANCO_URL, parse_tranco)[:opts["--top"]])

    excl_psl = sorted(domains & psl)
    excl_top = sorted((domains - psl) & top)
    keep = sorted(domains - psl - top)

    if len(keep) < opts["--min"]:
        log("ERRORE solo %d domini validi (minimo %d), tengo la lista precedente"
            % (len(keep), opts["--min"]))
        return 1

    with (TMP / ("%s.excluded.log" % NAME)).open("w") as f:
        f.write("# esclusi da %s il %s\n" % (NAME, time.strftime("%Y-%m-%d %H:%M")))
        f.writelines("suffisso_pubblico %s\n" % d for d in excl_psl)
        f.writelines("tranco_top%d %s\n" % (opts["--top"], d) for d in excl_top)
    tmp = conf.with_suffix(".conf.new")
    tmp.write_text("".join("local-zone: %s always_nxdomain\n" % d for d in keep))
    tmp.replace(conf)
    print("%s: %d domini (esclusi %d suffissi pubblici, %d siti popolari)"
          % (NAME, len(keep), len(excl_psl), len(excl_top)))
    return 0


NAME = "community_filter"
if __name__ == "__main__":
    sys.exit(main())
