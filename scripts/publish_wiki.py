#!/usr/bin/env python3
"""Veröffentlicht die Markdown-Seiten aus wiki/ im separaten Git-Wiki-Repository.

Der Haupt-Repository-Inhalt wird dabei nicht verändert. Das Skript löscht
keine unbekannten Wiki-Seiten. Es benötigt Git und Schreibrechte auf dem Wiki.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WIKI_SOURCE = Path(__file__).resolve().parents[1] / "wiki"


def run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Git fehlgeschlagen ({' '.join(args[:2])}): {result.stderr.strip()}")
    return result.stdout.strip()


def publish(remote: str, dry_run: bool = False) -> list[str]:
    pages = sorted(WIKI_SOURCE.glob("*.md"))
    if not pages or not (WIKI_SOURCE / "Home.md").is_file():
        raise RuntimeError("wiki/Home.md fehlt")
    if not remote.strip():
        raise RuntimeError("Wiki-Git-URL fehlt (Repository-URL mit .wiki.git)")
    if not remote.rstrip("/").endswith(".wiki.git"):
        raise RuntimeError("Nur eine explizite Wiki-Remote-URL mit .wiki.git ist erlaubt")
    names = [page.name for page in pages]
    if dry_run:
        return names
    if not shutil.which("git"):
        raise RuntimeError("git wurde nicht gefunden")
    with tempfile.TemporaryDirectory(prefix="mao-wiki-") as tmp:
        target = Path(tmp) / "wiki"
        run_git("clone", "--quiet", remote, str(target))
        for page in pages:
            shutil.copy2(page, target / page.name)
        run_git("add", "--", *names, cwd=target)
        if not run_git("status", "--porcelain", cwd=target):
            return names
        run_git("commit", "-m", "Dokumentation: Wiki-Seiten aktualisiert", cwd=target)
        run_git("push", "origin", "HEAD", cwd=target)
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wiki-Quellseiten zum Git-Wiki veröffentlichen")
    ap.add_argument("--remote", required=True, help="URL des Wiki-Repositories, endet auf .wiki.git")
    ap.add_argument("--dry-run", action="store_true", help="Seiten nur auflisten; keine Verbindung")
    args = ap.parse_args(argv)
    try:
        names = publish(args.remote, dry_run=args.dry_run)
        action = "Zur Veröffentlichung vorgesehen" if args.dry_run else "Veröffentlicht"
        print(f"{action}: {', '.join(names)}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"Fehler bei der Wiki-Veröffentlichung: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
