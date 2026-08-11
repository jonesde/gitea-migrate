#!/usr/bin/env python3
"""Gitea bulk mirror migration script.

Mirrors git repos to Gitea, preserving org/user structure as Gitea orgs
owned by jonesde. Idempotent - skips repos that already exist.

Usage:
  gitea-migrate.py REPOS_FILE

REPOS_FILE is a text file with one entry per line:
  owner/repo                           — GitHub (https://github.com/owner/repo.git)
  owner/repo https://clone/url.git     — Gitea owner/repo with custom clone URL
  https://host/owner/repo              — arbitrary host (optional trailing .git)
  https://host/group/sub/repo[.git]    — nested groups (e.g. GitLab); org=group, repo=last
Blank lines and # comments are ignored. Paths are resolved relative to the
current directory.
"""

import argparse
import os
import sys
import time
import requests
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent / "gitea.env"
DEFAULT_HOST = "github.com"
DEFAULT_SCHEME = "https"

# Load environment variables
for line in ENV_FILE.read_text().splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

GITEA_URL = os.environ["GITEA_URL"].rstrip("/")
TOKEN = os.environ["GITEA_TOKEN"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITEA_USER = os.environ["GITEA_USER"]
MIRROR_INTERVAL = os.environ["MIRROR_INTERVAL"]
DELAY_BETWEEN_REPOS = int(os.environ["DELAY_BETWEEN_REPOS"])

headers = {"Authorization": f"token {TOKEN}"}
github_headers = {"Accept": "application/vnd.github+json"}
if GITHUB_TOKEN:
    github_headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"


# ---------------------------------------------------------------------------
# Repo list loading
# ---------------------------------------------------------------------------

def parse_repo_entry(line: str) -> tuple[str, str, str]:
    """Parse a repo line into (clone_addr, owner, repo).

    Accepts:
      owner/repo
      owner/repo https://clone.host/path/repo.git  (Gitea owner/repo + custom clone URL)
      https://host/owner/repo
      https://host/owner/repo.git
      https://host/group/subgroup/repo[.git]  (nested; owner=first, repo=last)
    """
    # owner/repo <clone-url> — place under owner/repo, clone from URL
    parts_ws = line.split(None, 1)
    if (
        len(parts_ws) == 2
        and not parts_ws[0].startswith("http://")
        and not parts_ws[0].startswith("https://")
        and (parts_ws[1].startswith("http://") or parts_ws[1].startswith("https://"))
    ):
        owner_repo, clone_addr = parts_ws[0], parts_ws[1].rstrip("/")
        if clone_addr.endswith(".git"):
            pass
        else:
            clone_addr = clone_addr + ".git"
        if "/" not in owner_repo:
            raise ValueError(f"expected owner/repo before clone URL, got: {line!r}")
        owner, repo = owner_repo.split("/", 1)
        owner, repo = owner.strip(), repo.strip()
        if not owner or not repo or "/" in repo:
            raise ValueError(f"expected owner/repo before clone URL, got: {line!r}")
        return clone_addr, owner, repo

    if line.startswith("http://") or line.startswith("https://"):
        parsed = urlparse(line)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"invalid URL: {line!r}")
        if parsed.query or parsed.fragment:
            raise ValueError(f"URL must not include query or fragment: {line!r}")
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            raise ValueError(
                f"expected https://host/owner/repo or nested path, got: {line!r}"
            )
        owner, repo = parts[0], parts[-1]
        clone_addr = f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts)}.git"
        return clone_addr, owner, repo

    if "/" not in line:
        raise ValueError(f"expected owner/repo, got: {line!r}")
    owner, repo = line.split("/", 1)
    owner, repo = owner.strip(), repo.strip()
    if not owner or not repo or "/" in repo:
        raise ValueError(f"expected owner/repo, got: {line!r}")
    clone_addr = f"{DEFAULT_SCHEME}://{DEFAULT_HOST}/{owner}/{repo}.git"
    return clone_addr, owner, repo


def load_repos(path: Path) -> list[tuple[str, str, str]]:
    """Load repo entries from a text file.

    Format per line:
      owner/repo                         — defaults to github.com
      owner/repo https://clone/url.git   — Gitea owner/repo + custom clone URL
      https://host/owner/repo            — arbitrary host (optional .git)
      https://host/group/sub/repo[.git]  — nested groups; org=group, repo=last
    Blank lines and # comments ignored. Trailing comments stripped.
    Returns list of (clone_addr, owner, repo).
    """
    if not path.is_file():
        print(f"Error: repo list file not found: {path}", file=sys.stderr)
        sys.exit(1)

    repos: list[tuple[str, str, str]] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            repos.append(parse_repo_entry(line))
        except ValueError as e:
            print(f"Error: {path}:{lineno}: {e}", file=sys.stderr)
            sys.exit(1)

    if not repos:
        print(f"Error: no repos found in {path}", file=sys.stderr)
        sys.exit(1)
    return repos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gitea(path: str, method: str = "GET", **kwargs) -> dict | None:
    """Make a Gitea API call. Returns None on 404, raises on other errors."""
    # Large repos need longer timeouts for migration
    timeout = kwargs.pop("timeout", 120 if method != "POST" else 3600)
    resp = requests.request(
        method,
        f"{GITEA_URL}/api/v1{path}",
        headers=headers,
        timeout=timeout,
        **kwargs,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def github_get(path: str, **params) -> dict | list | None:
    """Make a GitHub API call."""
    resp = requests.get(
        f"https://api.github.com{path}",
        headers=github_headers,
        params=params,
        timeout=60,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Org / user management
# ---------------------------------------------------------------------------

def ensure_org(owner: str) -> int | None:
    """Ensure a Gitea org exists for the given source owner, owned by GITEA_USER.
    Returns the org's numeric ID, or None for personal account."""
    if owner.lower() == GITEA_USER.lower():
        return None  # Personal account, no org needed

    org = gitea(f"/orgs/{owner}")
    if org:
        return org["id"]

    print(f"  Creating org '{owner}' ...")
    org = gitea("/orgs", "POST", json={
        "username": owner,
        "description": f"Mirrored from {owner}",
    })
    print(f"  Org '{owner}' created")
    return org["id"]


# ---------------------------------------------------------------------------
# Repo migration
# ---------------------------------------------------------------------------

def mirror_repo(clone_addr: str, owner: str, repo: str, org_id: int | None) -> bool:
    """Mirror a single repo to Gitea. Returns True if migrated, False if skipped."""
    gitea_owner = owner if owner.lower() != GITEA_USER.lower() else GITEA_USER
    label = f"{owner}/{repo}"

    # Check if already exists
    if gitea(f"/repos/{gitea_owner}/{repo}"):
        print(f"  SKIP {label} (already exists)")
        return False

    print(f"  Migrating {label} from {clone_addr} ...")
    payload = {
        "repo_name": repo,
        "clone_addr": clone_addr,
        "mirror": True,
        "interval": MIRROR_INTERVAL,
        "private": False,
    }
    if org_id is not None:
        payload["uid"] = org_id
    try:
        gitea("/repos/migrate", "POST", json=payload)
        print(f"  OK {label}")
        return True
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        return True


# ---------------------------------------------------------------------------
# Batch listing helpers
# ---------------------------------------------------------------------------

def list_github_repos(owner: str) -> list[tuple[str, str]]:
    """List all public repos for a GitHub owner (user or org)."""
    repos = []
    page = 1
    while True:
        data = github_get(f"/users/{owner}/repos", per_page=100, page=page, type="all")
        if not data:
            break
        for r in data:
            repos.append((owner, r["name"]))
        if len(data) < 100:
            break
        page += 1
    return repos


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mirror git repos to Gitea, preserving org/user structure. "
            "Idempotent — skips repos that already exist."
        ),
        epilog=(
            "REPOS_FILE format (one per line):\n"
            "  owner/repo                         GitHub (default host)\n"
            "  owner/repo https://clone/url.git   Gitea owner/repo + custom clone URL\n"
            "  https://host/owner/repo            arbitrary host (optional .git)\n"
            "  https://host/group/sub/repo[.git]  nested (e.g. GitLab); org=group, repo=last\n"
            "Blank lines and # comments are ignored."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "repos_file",
        type=Path,
        help="text file listing repos as owner/repo or https://host/.../repo",
    )
    args = parser.parse_args()

    repos = load_repos(args.repos_file)

    print(f"Gitea: {GITEA_URL}")
    print(f"Owner: {GITEA_USER}")
    print(f"List:  {args.repos_file}")
    print(f"Repos: {len(repos)}")
    print()

    migrated = False
    for clone_addr, owner, repo in repos:
        if migrated:
            print(f"  (waiting {DELAY_BETWEEN_REPOS}s before next repo...)")
            time.sleep(DELAY_BETWEEN_REPOS)
        org_id = ensure_org(owner)
        migrated = mirror_repo(clone_addr, owner, repo, org_id)

    print("\nDone.")


if __name__ == "__main__":
    main()
