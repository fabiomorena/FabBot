#!/usr/bin/env python3
"""
pre-commit-Hook: blockiert Tokens in Git-Remote-URLs.

Hintergrund: Am 11.08.2026 lag ein GitHub-PAT im Klartext in der Remote-URL
(`https://user:ghp_...@github.com/...` in .git/config). Das Token war dabei
komplett überflüssig – `gh` war ohnehin über OAuth authentifiziert.

.git/config wird nicht versioniert, deshalb greift dort kein dateibasierter
Hook. Dieser Check prüft stattdessen den Repo-Zustand und läuft mit
always_run: true.

Nutzung:
    python git_remote_guard.py     # exit 1, wenn ein Remote Credentials enthält
"""

from __future__ import annotations

import re
import subprocess
import sys

# https://user:passwort@host – klassische Basic-Auth-Form
_MUSTER_USER_PASSWORT = re.compile(r"://[^/@\s]+:[^/@\s]*@")

# https://ghp_xxx@host – Token als Username, ohne Doppelpunkt
_MUSTER_TOKEN = re.compile(r"(ghp_|gho_|ghs_|ghu_|ghr_|github_pat_|glpat-)")


def _maskiere(url: str) -> str:
    """Ersetzt den Credential-Teil, damit der Hook das Secret nicht selbst ausgibt."""
    return re.sub(r"://[^/@\s]+@", "://<credentials>@", url)


def finde_unsichere_remotes(remote_ausgabe: str) -> list[tuple[str, str]]:
    """Findet Remotes mit eingebetteten Credentials.

    Erwartet die Ausgabe von `git remote -v`. Liefert (name, maskierte_url).
    Doppelte Einträge (fetch/push derselben URL) werden nur einmal gemeldet.
    """
    treffer: list[tuple[str, str]] = []
    gesehen: set[str] = set()
    for zeile in remote_ausgabe.splitlines():
        teile = zeile.split()
        if len(teile) < 2:
            continue
        name, url = teile[0], teile[1]
        if not (_MUSTER_USER_PASSWORT.search(url) or _MUSTER_TOKEN.search(url)):
            continue
        if name in gesehen:
            continue
        gesehen.add(name)
        treffer.append((name, _maskiere(url)))
    return treffer


def main() -> int:
    try:
        ergebnis = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"check_git_remote: 'git remote -v' fehlgeschlagen: {e}", file=sys.stderr)
        return 0  # kein Commit blockieren, wenn der Check selbst scheitert

    treffer = finde_unsichere_remotes(ergebnis.stdout)
    if not treffer:
        return 0

    print("FEHLER: Zugangsdaten in der Git-Remote-URL gefunden.\n", file=sys.stderr)
    for name, url in treffer:
        print(f"  {name}  {url}", file=sys.stderr)
    print(
        "\nDas Token steht im Klartext in .git/config und taucht bei jedem\n"
        "'git remote -v' auf. Bereinigen mit:\n\n"
        "  git remote set-url <name> https://github.com/<user>/<repo>.git\n"
        "  gh auth setup-git\n\n"
        "Danach das Token widerrufen: https://github.com/settings/tokens",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
