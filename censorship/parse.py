#!/usr/bin/env python3
"""
Parse official Italian DNS blacklists and output Unbound local-zone format.

Supported input formats:
  plain  — one entry per line; handles bare domains, hosts format (IP domain),
            and full URLs (scheme://domain/path)
  cncpo  — semicolon-separated CSV; domain is in column 2 (index 1),
            first row is header

Output (Unbound always_nxdomain):
  server:
  local-zone: "domain.example" always_nxdomain
  ...
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

_DOMAIN_RE = re.compile(
    r'^(?!-)(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$'
)


def _valid(domain: str) -> bool:
    return bool(_DOMAIN_RE.match(domain))


def _extract(token: str) -> str:
    """Extract a bare domain from a token (URL, domain, or IP:port)."""
    token = token.strip().lower().rstrip('/')
    if '://' in token:
        host = urlparse(token).hostname or ''
    else:
        host = token
    return host.split(':')[0].strip('.')


def parse_plain(path: Path) -> list[str]:
    """Plain-text list: bare domains, hosts format, or full URLs."""
    seen: dict[str, None] = {}
    with path.open(errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            # hosts format: skip the IP/address prefix, take last token
            token = parts[-1] if len(parts) > 1 else parts[0]
            d = _extract(token)
            if d:
                seen[d] = None
    return list(seen)


def parse_cncpo(path: Path) -> list[str]:
    """CNCPO CSV: semicolon-delimited, domain in column 2, first row is header."""
    seen: dict[str, None] = {}
    with path.open(errors='replace', newline='') as f:
        reader = csv.reader(f, delimiter=';')
        next(reader, None)  # skip header
        for row in reader:
            if len(row) > 1:
                d = _extract(row[1])
                if d:
                    seen[d] = None
    return list(seen)


def load_whitelist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(errors='replace') as f:
        return {
            line.strip().lower()
            for line in f
            if line.strip() and not line.startswith('#')
        }


def write_unbound(domains: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        f.write('server:\n')
        for d in domains:
            f.write(f'local-zone: "{d}" always_nxdomain\n')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Convert official Italian blacklists to Unbound local-zone format.'
    )
    parser.add_argument('-i', '--input',     required=True, help='Input file')
    parser.add_argument('-o', '--output',    required=True, help='Output .conf file')
    parser.add_argument('-f', '--format',    required=True,
                        choices=['plain', 'cncpo'],
                        help='Input format: plain (domains/hosts/URLs) or cncpo (CSV col 2)')
    parser.add_argument('-w', '--whitelist', metavar='FILE',
                        help='Optional whitelist (one domain per line)')
    args = parser.parse_args()

    in_path  = Path(args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        print(f'Error: input file not found: {in_path}', file=sys.stderr)
        sys.exit(1)

    domains = parse_cncpo(in_path) if args.format == 'cncpo' else parse_plain(in_path)

    invalid = sum(1 for d in domains if not _valid(d))
    if invalid:
        print(f'Skipping {invalid} invalid domain(s)', file=sys.stderr)
    domains = [d for d in domains if _valid(d)]

    if args.whitelist:
        wl = load_whitelist(Path(args.whitelist))
        before = len(domains)
        domains = [d for d in domains if d not in wl]
        if before - len(domains):
            print(f'Whitelist: excluded {before - len(domains)} domain(s)', file=sys.stderr)

    write_unbound(domains, out_path)
    print(f'{args.format}: {len(domains)} domains → {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
