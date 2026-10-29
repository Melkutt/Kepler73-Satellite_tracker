# -*- coding: utf-8 -*-
"""
Kepler73 - "is there a newer version on GitHub?"

Triggered only by the button in the About dialog (never automatically) and never
in offline-station mode. One small GET: the latest published release if there is
one, else the version number in ``backend/config.py`` on the ``main`` branch.
"""

import re
import json
import urllib.error
import urllib.request

from backend import netmode
from backend.config import APP_VERSION, GITHUB_REPO

REPO_URL = f"https://github.com/{GITHUB_REPO}"
_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_RAW_CONFIG = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/backend/config.py"


def _ver(text) -> tuple:
    """'v0.4.1' / '0.4' -> (0, 4, 1) / (0, 4, 0) for comparison."""
    nums = [int(n) for n in re.findall(r"\d+", str(text))[:3]]
    return tuple(nums) + (0,) * (3 - len(nums))


def _get(url: str, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Kepler73/{APP_VERSION}", "Accept": accept})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read(2_000_000)


def check() -> dict:
    """Compare the running version with GitHub. Never raises; the answer is
    {current, latest, update_available, source ('release'|'main'), url, name,
    error, offline}."""
    out = {"current": APP_VERSION, "latest": None, "update_available": False,
           "source": None, "url": REPO_URL, "name": "", "error": "", "offline": False}
    if netmode.is_offline():
        out.update(offline=True, error="Offline station mode is on – no check was made.")
        return out
    try:
        try:
            rel = json.loads(_get(_API_LATEST, "application/vnd.github+json"))
            out.update(latest=str(rel.get("tag_name") or "").lstrip("vV"),
                       source="release", url=rel.get("html_url") or REPO_URL + "/releases",
                       name=rel.get("name") or rel.get("tag_name") or "")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            # No release published yet: read the version from the main branch.
            text = _get(_RAW_CONFIG, "text/plain").decode("utf-8", "replace")
            m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
            if not m:
                raise ValueError("could not read the version number from the repository")
            out.update(latest=m.group(1), source="main")
        out["update_available"] = _ver(out["latest"]) > _ver(APP_VERSION)
    except urllib.error.HTTPError as e:
        out["error"] = ("GitHub's rate limit is reached – try again later"
                        if e.code in (403, 429) else f"GitHub answered HTTP {e.code}")
    except urllib.error.URLError as e:
        out["error"] = f"Could not reach GitHub ({e.reason})"
    except Exception as e:
        out["error"] = str(e) or type(e).__name__
    return out
