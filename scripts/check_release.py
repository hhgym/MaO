#!/usr/bin/env python3
"""Manuelle Preflight-Prüfung für Release-Tags und Python-Metadaten."""
from __future__ import annotations
import argparse
import subprocess
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]

def main(argv=None):
    parser = argparse.ArgumentParser(description='Git-Flow Release Preflight')
    parser.add_argument('tag', help='z. B. v0.4.0 oder v2.0.0-rc.1')
    args = parser.parse_args(argv)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf8'))['project']['version']
    if not args.tag.startswith('v') or Version(args.tag[1:]) != Version(version):
        print(f'Fehler: {args.tag} passt nicht zu {version}', file=sys.stderr)
        return 2
    print(f'Release-Version stimmt überein: {args.tag}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
