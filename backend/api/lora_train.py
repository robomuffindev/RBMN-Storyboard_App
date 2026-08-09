"""🚀 In-app LoRA training + ⚡ Autogen — the loop, wired into the app (v1.271.0).

Everything here has been run end-to-end four times by hand through the agent;
this module is that exact sequence as code. Two orchestrators, both background
threads with persisted stage files, both driving the app's OWN routes over
localhost (the same calls the agent made) plus the worker helper:

  TRAIN   export → upload zip → start Fizgig run → poll → score checkpoints
          (ArcFace on window-filtered previews vs the character's own refs —
          never loss) → install the pick into ComfyUI → done, LoRA usable in 🧬.

  AUTOGEN from one promoted front reference: generate missing views → create a
          face_heavy dataset (optional wardrobe variations so the base outfit
          isn't baked dominant) → render → caption → QC → repair flagged →
          ONE targeted re-render round on below-match rows → export with the
          0.25 likeness floor → TRAIN.

The trainer host is the Krea 2 / Fizgig box (DHCP — editable in Settings and
in 🧬). State survives restarts: _libraries/lora/_train/<ds>.json and
_autogen/<slug>.json; a poll re-attaches to a live helper run.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import settings as cfg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lora", tags=["lora-train"])

APP = "http://127.0.0.1:8899"
# ⚠ cfg.project_dir is overridden from the DB after import — anchor on
# lora.py's import-time root so all lora state lives under ONE tree.
from backend.api.lora import _DS_ROOT as _LORA_DS_ROOT  # noqa: E402
_TRAIN_DIR = _LORA_DS_ROOT.parent / "_train"
_AUTO_DIR = _LORA_DS_ROOT.parent / "_autogen"
_ACTIVE: Dict[str, bool] = {}        # "train:<ds>" / "auto:<slug>" -> thread alive


# ── trainer settings (host lives with forge's; token/port here) ─────────────
def _tsettings() -> dict:
    tr = next((h for h in _helpers_list() if h.get("is_trainer")), None) \
        or _helpers_list()[0]
    return {"host": str(tr.get("host")), "port": int(tr.get("port") or 8765),
            "token": str(tr.get("token") or "49ae12e57c0949158b2efb4edfb0ac49")}


def _helpers_list() -> list:
    """Worker registry in the forge settings store. Legacy single-trainer keys
    migrate into it on first read; the ⭐ trainer entry keeps them in sync so
    forge's Krea2 host and the train pipeline never diverge."""
    from backend.api.forge import _fsettings, _fsettings_save
    st = _fsettings()
    hs = st.get("helpers")
    if not isinstance(hs, list) or not hs:
        hs = [{"id": "trainer", "name": "Training box",
               "host": str(st.get("krea2_host") or "192.168.12.201"),
               "port": int(st.get("helper_port") or 8765),
               "token": str(st.get("helper_token") or
                            "49ae12e57c0949158b2efb4edfb0ac49"),
               "is_trainer": True}]
        st["helpers"] = hs
        _fsettings_save(st)
    return hs


def _helpers_save(hs: list) -> None:
    from backend.api.forge import _fsettings, _fsettings_save, _UNET_CACHE
    st = _fsettings()
    st["helpers"] = hs
    tr = next((h for h in hs if h.get("is_trainer")), None)
    if tr:                                   # keep legacy keys (forge reads them)
        st["krea2_host"] = tr["host"]
        st["helper_port"] = int(tr.get("port") or 8765)
        st["helper_token"] = tr.get("token") or st.get("helper_token")
        _UNET_CACHE.clear()
    _fsettings_save(st)


def _hj_at(host: str, port: int, token: str, path: str,
           body: Optional[dict] = None, timeout: float = 30.0):
    sep = "&" if "?" in path else "?"
    url = f"http://{host}:{port}{path}{sep}token={token}"
    if body is not None:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _helper(path: str) -> str:
    t = _tsettings()
    sep = "&" if "?" in path else "?"
    return f"http://{t['host']}:{t['port']}{path}{sep}token={t['token']}"


def _hj(path: str, body: Optional[dict] = None, raw: Optional[bytes] = None,
        timeout: float = 120.0):
    url = _helper(path)
    if raw is not None:
        req = urllib.request.Request(url, data=raw, method="POST",
                                     headers={"Content-Type": "application/zip"})
    elif body is not None:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _app(method: str, path: str, body: Optional[dict] = None, timeout: float = 900.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(APP + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


@router.get("/trainer-settings")
async def trainer_settings():
    t = _tsettings()
    online, helper_version, paths = False, None, {}
    try:
        import asyncio
        h = await asyncio.to_thread(_hj, "/health", None, None, 6.0)
        online, helper_version = bool(h.get("ok")), h.get("helper")
        # The PATHS live in the helper's own config on the box — the source of
        # truth when a machine has several ComfyUI / Fizgig installs.
        cfg_ = await asyncio.to_thread(_hj, "/config", None, None, 8.0)
        paths = {"comfy_root": (cfg_.get("comfy") or {}).get("root"),
                 "comfy_start_cmd": (cfg_.get("comfy") or {}).get("start_cmd"),
                 "fizgig_root": (cfg_.get("fizgig") or {}).get("root"),
                 "fizgig_python": (cfg_.get("fizgig") or {}).get("python")}
    except Exception:  # noqa: BLE001
        pass
    return {**{k: t[k] for k in ("host", "port")}, "token_set": bool(t["token"]),
            "online": online, "helper_version": helper_version, "paths": paths}


@router.get("/trainer-detect")
async def trainer_detect():
    """The helper scans ITS box for every ComfyUI / Fizgig install it can find."""
    import asyncio
    try:
        return await asyncio.to_thread(_hj, "/detect", None, None, 30.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"helper unreachable: {e}")


class TrainerPathsIn(BaseModel):
    comfy_root: Optional[str] = None
    comfy_start_cmd: Optional[str] = None
    fizgig_root: Optional[str] = None
    fizgig_python: Optional[str] = None


@router.put("/trainer-paths")
async def trainer_paths_put(body: TrainerPathsIn):
    """Write install paths into the HELPER's config on the box (save_config
    merges one level deep, so only the fields sent change)."""
    patch: dict = {}
    if body.comfy_root is not None or body.comfy_start_cmd is not None:
        patch["comfy"] = {}
        if body.comfy_root is not None:
            patch["comfy"]["root"] = body.comfy_root.strip()
        if body.comfy_start_cmd is not None:
            patch["comfy"]["start_cmd"] = body.comfy_start_cmd.strip()
    if body.fizgig_root is not None or body.fizgig_python is not None:
        patch["fizgig"] = {}
        if body.fizgig_root is not None:
            patch["fizgig"]["root"] = body.fizgig_root.strip()
        if body.fizgig_python is not None:
            patch["fizgig"]["python"] = body.fizgig_python.strip()
    if not patch:
        raise HTTPException(400, "nothing to change")
    import asyncio
    try:
        cfg_ = await asyncio.to_thread(_hj, "/config", patch, None, 30.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"helper unreachable: {e}")
    return {"ok": True, "comfy": cfg_.get("comfy"), "fizgig": cfg_.get("fizgig")}


class TrainerSettingsIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    token: Optional[str] = None


@router.put("/trainer-settings")
async def trainer_settings_put(body: TrainerSettingsIn):
    from backend.api.forge import _fsettings, _fsettings_save, _UNET_CACHE
    s = _fsettings()
    if body.host is not None:
        s["krea2_host"] = body.host.strip().replace("http://", "").split(":")[0]
        _UNET_CACHE.clear()
    if body.port is not None:
        s["helper_port"] = int(body.port)
    if body.token is not None and body.token.strip():
        s["helper_token"] = body.token.strip()
    _fsettings_save(s)
    return {"ok": True, **{k: v for k, v in _tsettings().items() if k != "token"}}


class HelperIn(BaseModel):
    name: Optional[str] = None
    host: str
    port: int = 8765
    token: Optional[str] = None


class HelperUpdateIn(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    token: Optional[str] = None
    is_trainer: Optional[bool] = None


def _probe_helper(h: dict) -> dict:
    """/health is UNAUTHENTICATED on the helper (it feeds the landing page), so
    reachability and auth are two different facts — report both honestly."""
    out = {k: h.get(k) for k in ("id", "name", "host", "port", "is_trainer")}
    out["token_set"] = bool(h.get("token"))
    host, port, tok = h["host"], int(h.get("port") or 8765), h.get("token") or ""
    try:
        hl = _hj_at(host, port, tok, "/health", None, 5.0)
        out["reachable"] = True
        out["helper_version"] = hl.get("helper")
        out["gpu"] = (hl.get("gpu") or {}).get("name")
    except Exception as e:  # noqa: BLE001
        out.update(reachable=False, online=False,
                   error=f"unreachable ({type(e).__name__})")
        return out
    try:
        cfg_ = _hj_at(host, port, tok, "/config", None, 6.0)
        out["online"] = True
        out["paths"] = {"comfy_root": (cfg_.get("comfy") or {}).get("root"),
                        "comfy_start_cmd": (cfg_.get("comfy") or {}).get("start_cmd"),
                        "fizgig_root": (cfg_.get("fizgig") or {}).get("root"),
                        "fizgig_python": (cfg_.get("fizgig") or {}).get("python")}
        out["has_fizgig"] = bool((cfg_.get("fizgig") or {}).get("root"))
    except urllib.error.HTTPError as e:
        out["online"] = False
        out["error"] = ("reachable but WRONG TOKEN — paste THIS box's token (the "
                        "'TOKEN …' line in its helper console banner)") \
            if e.code == 401 else f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        out["online"] = False
        out["error"] = f"config probe failed ({type(e).__name__})"
    return out


@router.get("/helpers")
async def helpers_list():
    import asyncio
    hs = _helpers_list()
    probed = await asyncio.gather(*(asyncio.to_thread(_probe_helper, h) for h in hs))
    return {"helpers": list(probed)}


@router.post("/helpers")
async def helpers_add(body: HelperIn):
    hs = _helpers_list()
    host = body.host.strip().replace("http://", "").split(":")[0]
    if not host:
        raise HTTPException(400, "host required")
    hid = f"wk{len(hs)}{host.split('.')[-1]}"
    hs.append({"id": hid, "name": (body.name or host).strip(), "host": host,
               "port": int(body.port or 8765),
               "token": (body.token or "").strip() or
               "49ae12e57c0949158b2efb4edfb0ac49",
               "is_trainer": False})
    _helpers_save(hs)
    return {"ok": True, "id": hid}


@router.put("/helpers/{hid}")
async def helpers_update(hid: str, body: HelperUpdateIn):
    hs = _helpers_list()
    h = next((x for x in hs if x.get("id") == hid), None)
    if not h:
        raise HTTPException(404, "no such worker")
    if body.name is not None:
        h["name"] = body.name.strip()
    if body.host is not None:
        h["host"] = body.host.strip().replace("http://", "").split(":")[0]
    if body.port is not None:
        h["port"] = int(body.port)
    if body.token is not None and body.token.strip():
        h["token"] = body.token.strip()
    if body.is_trainer:
        for x in hs:
            x["is_trainer"] = x.get("id") == hid
    _helpers_save(hs)
    return {"ok": True}


@router.post("/helpers/{hid}/delete")
async def helpers_delete(hid: str):
    hs = _helpers_list()
    hs2 = [x for x in hs if x.get("id") != hid]
    if not hs2:
        raise HTTPException(400, "cannot delete the last worker")
    if not any(x.get("is_trainer") for x in hs2):
        hs2[0]["is_trainer"] = True
    _helpers_save(hs2)
    return {"ok": True}


@router.put("/helpers/{hid}/paths")
async def helpers_paths(hid: str, body: TrainerPathsIn):
    hs = _helpers_list()
    h = next((x for x in hs if x.get("id") == hid), None)
    if not h:
        raise HTTPException(404, "no such worker")
    patch: dict = {}
    if body.comfy_root is not None or body.comfy_start_cmd is not None:
        patch["comfy"] = {}
        if body.comfy_root is not None:
            patch["comfy"]["root"] = body.comfy_root.strip()
        if body.comfy_start_cmd is not None:
            patch["comfy"]["start_cmd"] = body.comfy_start_cmd.strip()
    if body.fizgig_root is not None or body.fizgig_python is not None:
        patch["fizgig"] = {}
        if body.fizgig_root is not None:
            patch["fizgig"]["root"] = body.fizgig_root.strip()
        if body.fizgig_python is not None:
            patch["fizgig"]["python"] = body.fizgig_python.strip()
    if not patch:
        raise HTTPException(400, "nothing to change")
    import asyncio
    try:
        cfg_ = await asyncio.to_thread(_hj_at, h["host"], int(h.get("port") or 8765),
                                       h.get("token") or "", "/config", patch, 30.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"worker helper unreachable: {e}")
    return {"ok": True, "comfy": cfg_.get("comfy"), "fizgig": cfg_.get("fizgig")}


@router.get("/helpers/{hid}/detect")
async def helpers_detect(hid: str):
    hs = _helpers_list()
    h = next((x for x in hs if x.get("id") == hid), None)
    if not h:
        raise HTTPException(404, "no such worker")
    import asyncio
    try:
        return await asyncio.to_thread(_hj_at, h["host"], int(h.get("port") or 8765),
                                       h.get("token") or "", "/detect", None, 30.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"worker helper unreachable: {e}")


# ── state files ──────────────────────────────────────────────────────────────
def _state_load(fp: Path) -> dict:
    if fp.exists():
        try:
            return json.loads(fp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _state_save(fp: Path, st: dict) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), "utf-8")
    tmp.replace(fp)


def _stage(fp: Path, st: dict, stage: str, detail: str = "") -> None:
    st["stage"] = stage
    st["detail"] = detail
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _state_save(fp, st)
    logger.info("lora-train %s: %s %s", fp.stem, stage, detail)


# ── checkpoint scoring (the never-loss pick, internal) ───────────────────────
def _score_and_pick(ds_id: str, char_slug: str, run: dict) -> dict:
    from backend.services import likeness as lk
    from backend.api.klein3 import _load as _load_char, _cdir, _refs_by_tag
    if not lk.available():
        raise RuntimeError(f"ArcFace unavailable: {lk.health().get('error')}")
    c = _load_char(char_slug)
    embs = []
    for tag in ("face", "front"):
        for r in _refs_by_tag(c, tag):
            fp = _cdir(char_slug) / "refs" / f"{r['id']}.png"
            if fp.exists():
                e = lk.embed(fp)
                if e is not None:
                    embs.append(e)
    if not embs:
        raise RuntimeError("no usable reference face for scoring")
    t0s, t1s = str(run.get("started") or ""), str(run.get("finished") or "")
    shots = []
    for x in run.get("artifacts") or []:
        m = re.search(r"_e(\d{6})_(\d\d)_", x["name"])
        if not m or m.group(2) != "00" or x.get("kind") != "image":
            continue
        mod = str(x.get("modified") or "")
        if t0s and mod and (mod < t0s or (t1s and mod > t1s)):
            continue
        shots.append((int(m.group(1)), x["name"]))
    shots.sort()
    if not shots:
        raise RuntimeError("no previews in the run window — cannot score")
    tmp = Path(tempfile.mkdtemp(prefix="apptrain_"))
    rows = []
    for ep, name in shots:
        fp = tmp / f"e{ep:03d}.png"
        url = _helper(f"/runs/{run['id']}/artifacts/{urllib.parse.quote(name)}")
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                fp.write_bytes(r.read())
            s = lk.score(fp, embs)
        except Exception:  # noqa: BLE001
            s = None
        if s is not None:
            rows.append({"epoch": ep, "score": round(s, 4)})
    if not rows:
        raise RuntimeError("no preview could be scored")
    best = max(rows, key=lambda r: r["score"])
    return {"scores": rows, "best_epoch": best["epoch"], "best_score": best["score"]}


# ── the TRAIN pipeline ───────────────────────────────────────────────────────
def _train_pipeline(ds_id: str, opts: dict) -> None:
    fp = _TRAIN_DIR / f"{ds_id}.json"
    st = {"dataset": ds_id, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "error": None, "installed": None}
    try:
        ds = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
        char_slug = ds.get("char_slug")
        run_id = opts.get("run_id")
        if not run_id:
            _stage(fp, st, "export", "min_likeness 0.25")
            exp = _app("POST", f"/api/lora/datasets/{ds_id}/export",
                       {"min_likeness": 0.25}, timeout=900)
            zip_name = exp["file"]
            zip_fp = _LORA_DS_ROOT / ds_id / "exports" / zip_name
            if not zip_fp.exists():
                raise RuntimeError(f"export missing on disk: {zip_fp}")
            _stage(fp, st, "upload", f"{zip_name} ({zip_fp.stat().st_size} bytes)")
            up = _hj(f"/datasets/{ds_id}", raw=zip_fp.read_bytes(), timeout=900)
            if not up.get("runnable"):
                raise RuntimeError(f"helper rejected the dataset: {json.dumps(up)[:200]}")
            _stage(fp, st, "train", "starting Fizgig run (ComfyUI will be stopped)")
            run = _hj("/runs", body={"dataset": ds_id}, timeout=300)
            run_id = run["id"]
            st["run_id"] = run_id
            _stage(fp, st, "train", f"run {run_id} running — hours, not minutes")
        else:
            st["run_id"] = run_id
            _stage(fp, st, "train", f"attaching to existing run {run_id}")

        while True:
            time.sleep(60)
            try:
                run = _hj(f"/runs/{run_id}?kind=weights", timeout=60)
            except Exception as e:  # noqa: BLE001
                _stage(fp, st, "train", f"helper unreachable ({e}) — box asleep or IP "
                                        "moved; will keep retrying")
                continue
            if run.get("status") == "failed" or (run.get("rc") not in (None, 0)):
                raise RuntimeError(f"training failed rc={run.get('rc')}: "
                                   f"{str(run.get('error'))[:300]}")
            n = sum(1 for a in (run.get("artifacts") or [])
                    if a.get("kind") == "weights" and "-state" not in a["name"])
            _stage(fp, st, "train", f"run {run_id}: {run.get('status')} — "
                                    f"{n} checkpoints so far")
            if run.get("status") == "done":
                break

        _stage(fp, st, "score", "ArcFace on window-filtered previews (never loss)")
        run_full = _hj(f"/runs/{run_id}", timeout=120)
        pick = _score_and_pick(ds_id, char_slug, run_full)
        st["pick"] = pick
        _stage(fp, st, "install",
               f"epoch {pick['best_epoch']} ({pick['best_score']:.4f})")
        stamp = time.strftime("%m%d%H%M")
        dest = f"{char_slug}-{stamp}-e{pick['best_epoch']}.safetensors"
        inst = _hj(f"/runs/{run_id}/install-lora",
                   body={"name": f"{ds_id}-{pick['best_epoch']:06d}.safetensors",
                         "dest_name": dest}, timeout=300)
        st["installed"] = dest
        st["installed_path"] = inst.get("installed")
        try:
            _hj("/comfy/start", body={}, timeout=60)   # hand the GPU back
        except Exception:  # noqa: BLE001
            pass
        _stage(fp, st, "done", f"{dest} installed — pick it in 🧬 (Krea 2 engine)")
    except Exception as e:  # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"
        _stage(fp, st, "error", st["error"])
    finally:
        _ACTIVE.pop(f"train:{ds_id}", None)


class TrainIn(BaseModel):
    run_id: Optional[str] = None     # attach/score an existing helper run


@router.post("/datasets/{ds_id}/train")
async def train(ds_id: str, body: TrainIn):
    _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)     # 404s early on bad id
    key = f"train:{ds_id}"
    if _ACTIVE.get(key):
        raise HTTPException(409, "a training pipeline is already running for this dataset")
    try:
        _hj("/health", None, None, 8.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"trainer helper unreachable at "
                                 f"{_tsettings()['host']}:{_tsettings()['port']} — check "
                                 f"the IP in Settings (it moves). ({e})")
    _ACTIVE[key] = True
    threading.Thread(target=_train_pipeline, args=(ds_id, body.model_dump()),
                     daemon=True).start()
    return {"started": True, "dataset": ds_id, "attach_run": body.run_id}


@router.get("/datasets/{ds_id}/train/status")
async def train_status(ds_id: str):
    st = _state_load(_TRAIN_DIR / f"{ds_id}.json")
    st["active"] = bool(_ACTIVE.get(f"train:{ds_id}"))
    return st or {"stage": "idle"}


# ── the AUTOGEN pipeline ─────────────────────────────────────────────────────
def _wait(pred, timeout_s: float, every: float = 15.0, desc: str = ""):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {desc or 'condition'}")


def _autogen_pipeline(slug: str, opts: dict) -> None:
    fp = _AUTO_DIR / f"{slug}.json"
    st = {"character": slug, "options": opts, "error": None, "dataset": None,
          "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        # 1. missing views ---------------------------------------------------
        c = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
        missing = c.get("missing_views") or []
        if missing:
            _stage(fp, st, "views", f"generating missing views: {missing}")
            _app("POST", f"/api/klein3/characters/{slug}/views/generate", {}, timeout=120)

            def _views_done():
                cc = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
                jobs = cc.get("jobs") or {}
                busy = any(j.get("status") == "running" for j in jobs.values())
                return cc if not busy else None
            c = _wait(_views_done, 3600, 20, "missing views")
            still = c.get("missing_views") or []
            if still:
                _stage(fp, st, "views", f"warning: views still missing {still} — "
                                        "continuing (angle-matched fallback will cope)")
        # 2. dataset ---------------------------------------------------------
        outfits = []
        if opts.get("outfit_mode") == "flexible":
            _stage(fp, st, "wardrobe", "asking the vision model for outfit variations")
            try:
                w = _app("POST", f"/api/lora/characters/{slug}/wardrobe",
                         {"count": int(opts.get("outfit_count") or 3)}, timeout=300)
                outfits = (w.get("outfits") or [])[:int(opts.get("outfit_count") or 3)]
                st["outfits"] = outfits
            except Exception as e:  # noqa: BLE001
                _stage(fp, st, "wardrobe", f"wardrobe suggest failed ({e}) — "
                                           "continuing with the base outfit only")
        _stage(fp, st, "dataset", "creating face_heavy dataset")
        ds = _app("POST", "/api/lora/datasets",
                  {"char_slug": slug, "name": f"{slug}-auto",
                   "preset": "face_heavy", "count": int(opts.get("total") or 40),
                   "outfits": outfits}, timeout=120)
        ds_id = ds["id"]
        st["dataset"] = ds_id
        total = len(ds.get("items") or []) or int(opts.get("total") or 40)

        # 3. render ------------------------------------------------------------
        _stage(fp, st, "render", f"0/{total}")
        _app("POST", f"/api/lora/datasets/{ds_id}/generate", {}, timeout=120)

        def _rendered():
            d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
            done = sum(1 for x in d.get("items", []) if x.get("status") == "done")
            _stage(fp, st, "render", f"{done}/{total}")
            return d if done >= total else None
        _wait(_rendered, 4 * 3600, 30, "renders")

        # 4. caption + qc ------------------------------------------------------
        _stage(fp, st, "caption", "")
        _app("POST", f"/api/lora/datasets/{ds_id}/caption", {"overwrite": True},
             timeout=1800)
        _stage(fp, st, "qc", "full pass (vision + ArcFace + wardrobe + crop)")
        _app("POST", f"/api/lora/datasets/{ds_id}/qc", {"overwrite": True}, timeout=120)

        def _qc_done():
            d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
            n = sum(1 for x in d.get("items", [])
                    if (x.get("qc") or {}).get("checked_at"))
            _stage(fp, st, "qc", f"{n}/{total}")
            return d if n >= total else None
        d = _wait(_qc_done, 3600, 20, "qc")

        # 5. repair flagged (up to 2 rounds), then ONE below-match reroll ------
        flagged = [x["id"] for x in d.get("items", [])
                   if (x.get("qc") or {}).get("ok") is False]
        if flagged:
            _stage(fp, st, "repair", f"{len(flagged)} flagged rows, up to 2 rounds")
            _app("POST", f"/api/lora/datasets/{ds_id}/repair",
                 {"rounds": 2, "qc_after": True}, timeout=120)

            def _repair_done():
                dd = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
                fl = [x["id"] for x in dd.get("items", [])
                      if (x.get("qc") or {}).get("ok") is False]
                # repair route runs async; treat quiescence as: all rows have a
                # fresh checked_at AND flag count stopped changing (checked via
                # two consecutive equal counts handled by _wait's polling).
                _stage(fp, st, "repair", f"{len(fl)} still flagged")
                return dd if not fl else None
            try:
                d = _wait(_repair_done, 3600, 30, "repair")
            except TimeoutError:
                d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
                _stage(fp, st, "repair", "timeout — continuing with what we have")
        _app("POST", f"/api/lora/datasets/{ds_id}/likeness", {}, timeout=900)
        d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
        weak = [x["id"] for x in d.get("items", [])
                if isinstance((x.get("qc") or {}).get("identity_score"), (int, float))
                and x["qc"]["identity_score"] < 0.45 and x.get("angle") != "back"]
        if weak:
            _stage(fp, st, "reroll", f"one round on {len(weak)} below-match rows")
            _app("POST", f"/api/lora/datasets/{ds_id}/generate",
                 {"item_ids": weak, "overwrite": True}, timeout=120)

            def _rerolled():
                dd = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
                done = sum(1 for x in dd.get("items", [])
                           if x.get("status") == "done")
                return dd if done >= total else None
            _wait(_rerolled, 3600, 30, "reroll renders")
            _app("POST", f"/api/lora/datasets/{ds_id}/qc",
                 {"item_ids": weak, "overwrite": True}, timeout=120)
            time.sleep(90)
            _app("POST", f"/api/lora/datasets/{ds_id}/caption",
                 {"item_ids": weak, "overwrite": True}, timeout=900)
            _app("POST", f"/api/lora/datasets/{ds_id}/likeness", {}, timeout=900)

        # 6. train (unless dataset_only) --------------------------------------
        if opts.get("dataset_only"):
            _stage(fp, st, "done", f"dataset {ds_id} ready (training skipped by option)")
            return
        _stage(fp, st, "train", f"handing {ds_id} to the training pipeline")
        _train_pipeline(ds_id, {})
        tr = _state_load(_TRAIN_DIR / f"{ds_id}.json")
        if tr.get("error"):
            raise RuntimeError(f"training pipeline: {tr['error']}")
        st["installed"] = tr.get("installed")
        st["pick"] = tr.get("pick")
        _stage(fp, st, "done", f"{tr.get('installed')} installed — usable in 🧬")
    except Exception as e:  # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"
        _stage(fp, st, "error", st["error"])
    finally:
        _ACTIVE.pop(f"auto:{slug}", None)


class AutogenIn(BaseModel):
    char_slug: str
    outfit_mode: str = "dominant"        # dominant | flexible
    outfit_count: int = 3
    total: int = 40
    dataset_only: bool = False           # stop after the dataset (no training)


@router.post("/autogen")
async def autogen(body: AutogenIn):
    c = _app("GET", f"/api/klein3/characters/{body.char_slug}", timeout=60)
    if not c.get("has_base") and "front" in (c.get("missing_views") or []):
        raise HTTPException(409, "this character needs a front reference/base first — "
                                 "promote one in 🧬 Text 2 Image")
    key = f"auto:{body.char_slug}"
    if _ACTIVE.get(key):
        raise HTTPException(409, "autogen already running for this character")
    if not body.dataset_only:
        try:
            _hj("/health", None, None, 8.0)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"trainer helper unreachable — fix the IP in Settings "
                                     f"or run with dataset_only. ({e})")
    _ACTIVE[key] = True
    threading.Thread(target=_autogen_pipeline,
                     args=(body.char_slug, body.model_dump()), daemon=True).start()
    return {"started": True, "character": body.char_slug}


@router.get("/autogen/{slug}/status")
async def autogen_status(slug: str):
    st = _state_load(_AUTO_DIR / f"{slug}.json")
    st["active"] = bool(_ACTIVE.get(f"auto:{slug}"))
    return st or {"stage": "idle"}
