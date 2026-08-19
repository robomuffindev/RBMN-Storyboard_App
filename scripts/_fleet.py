"""🔧 The worker registry, WITHOUT importing the app (v1.277.37).

`from backend.api.lora_train import _helpers_list` drags in the whole FastAPI
app, so any tool using it only runs under `venv\\Scripts\\python.exe`. That is
fine for the agent and wrong for a human at a prompt — and these are exactly the
tools you reach for when something is broken, which is the worst moment to hit

    ModuleNotFoundError: No module named 'fastapi'

The registry itself is plain JSON on disk (`<project_dir>/_libraries/forge/
settings.json`, key `helpers`), so this reads it with stdlib only, and falls
back to the app import if the file is not where we expect.

`project_dir` comes from `.env` (`PROJECT_DIR=`), expanded like backend/config
does, defaulting to `~/RBMN-Projects`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent


def project_dir() -> Path:
    """Where the libraries live — .env first, then the same default as the app."""
    raw = os.environ.get("PROJECT_DIR", "")
    if not raw:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("PROJECT_DIR="):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return Path(raw or "~/RBMN-Projects").expanduser()


def helpers() -> List[dict]:
    """[{id,name,host,port,token,is_trainer}] — the Settings → 🔧 Worker Helpers rows."""
    fp = project_dir() / "_libraries" / "forge" / "settings.json"
    try:
        st = json.loads(fp.read_text("utf-8"))
        hs = st.get("helpers")
        if isinstance(hs, list) and hs:
            return hs
        # the legacy single-trainer shape, before the registry existed
        if st.get("krea2_host"):
            return [{"id": "trainer", "name": "Training box",
                     "host": str(st["krea2_host"]),
                     "port": int(st.get("helper_port") or 8765),
                     "token": str(st.get("helper_token") or ""),
                     "is_trainer": True}]
    except Exception:                                            # noqa: BLE001
        pass
    # last resort: ask the app (works under the venv, needs fastapi installed)
    import sys
    sys.path.insert(0, str(ROOT))
    from backend.api.lora_train import _helpers_list
    return _helpers_list()
