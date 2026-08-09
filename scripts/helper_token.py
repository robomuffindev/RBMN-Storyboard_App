"""The worker-helper token, in ONE place, out of the source.

v1.276.4.  The token was hard-coded as a default in seven tracked files. That
is a shared credential sitting in a repo that is about to be pushed to GitHub,
which is the wrong place for it regardless of how private the repo is or how
LAN-only the helper is.

Resolution order, first hit wins:

  1. RBMN_HELPER_TOKEN in the environment
  2. `helper_token.txt` next to this file  (gitignored)
  3. `token` in <project_dir>/_libraries/forge/settings.json, which is where the
     app already keeps the trainer settings the UI edits
  4. "" — and every caller treats an empty token as "ask the user", rather than
     silently sending a blank one and reporting a confusing 401

Nothing about the helper changes: it still takes `?token=`. This only moves
WHERE the value comes from.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_SIDECAR = _HERE / "helper_token.txt"


def _from_settings() -> Optional[str]:
    """The trainer token the app's own Settings page already stores."""
    try:
        from backend.config import settings as cfg
        fp = Path(cfg.project_dir) / "_libraries" / "forge" / "settings.json"
        if fp.exists():
            d = json.loads(fp.read_text("utf-8"))
            for key in ("trainer_token", "helper_token", "token"):
                v = str((d or {}).get(key) or "").strip()
                if v:
                    return v
    except Exception:  # noqa: BLE001 — a missing app import is normal in scripts
        pass
    return None


def helper_token(default: str = "") -> str:
    v = os.environ.get("RBMN_HELPER_TOKEN", "").strip()
    if v:
        return v
    try:
        if _SIDECAR.exists():
            v = _SIDECAR.read_text("utf-8").strip()
            if v:
                return v
    except OSError:
        pass
    return _from_settings() or default


TOKEN = helper_token()


if __name__ == "__main__":
    t = helper_token()
    print(f"helper token: {'<set, %d chars>' % len(t) if t else '<EMPTY — set RBMN_HELPER_TOKEN or write scripts/helper_token.txt>'}")
