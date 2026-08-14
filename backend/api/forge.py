"""🧬 Text 2 Image — the master initial character creation mode (v1.269–1.270).

The entry point when a character does NOT start from outside reference images:
name the character first (resumable), generate candidate images from a text
prompt on a chosen model, iterate on a favorite with Klein edit instructions
("change the hair to…", "make the legs longer…"), keep everything in a master
gallery, and finally promote the finished image as the character's FRONT
reference (+ active base) — from where the existing Klein 3.0 flow generates
the other angles, wardrobe, poses, LoRA datasets and character sheets.

Engines:
  klein        FLUX.2 Klein 9B — t2i with 0 refs, edit graph with 1-5 refs.
               Renders fan across the app's klein workers.
  krea2_turbo  Krea 2 Turbo — pure t2i, and the ONLY engine that takes a
               character LoRA (that is what we train them for). Renders run
               DIRECTLY on the Krea 2 box (the training box) with the same
               core-node graph the TURBO exam validated — that box holds the
               installed LoRAs and lacks the decorator custom nodes. The box
               is on DHCP; its IP is editable via PUT /krea2-host.

Also owns the character LORE store (char.json["lore"]) — the Story Builder
substrate. Storage: <chars>/<slug>/forge/<image-id>.png + forge.json.
All prompts AFFIRMATIVE (Klein: no negative node, cfg=1 — standing rule 2).
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import random
import shutil
import threading
import time
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/forge", tags=["forge"])

_RUNS: Dict[str, dict] = {}          # slug -> live generate/edit status
_FORGE_DIR = Path(cfg.project_dir) / "_libraries" / "forge"
_SETTINGS_FP = _FORGE_DIR / "settings.json"
_UNET_CACHE: Dict[str, str] = {}     # host -> chosen krea2 unet file
_OV_CACHE: Dict[str, Any] = {}       # studio-overview box-lora cache


# ── forge settings (the Krea 2 box moves — DHCP) ─────────────────────────────
def _fsettings() -> dict:
    if _SETTINGS_FP.exists():
        try:
            return json.loads(_SETTINGS_FP.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _fsettings_save(d: dict) -> None:
    _FORGE_DIR.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FP.write_text(json.dumps(d, indent=2), "utf-8")


def _krea2_host() -> str:
    return str(_fsettings().get("krea2_host") or "192.168.12.201")


# ── engine registry ──────────────────────────────────────────────────────────
def _engines_available() -> List[dict]:
    from backend.api.klein2 import _WORKFLOWS_DIR
    out = []
    if (_WORKFLOWS_DIR / "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json").exists():
        out.append({
            "key": "klein", "name": "FLUX.2 Klein 9B", "supports_refs": True,
            "max_refs": 5, "supports_lora": False,
            "note": "0 refs = pure text-to-image; 1-5 refs = reference-guided. "
                    "The edit loop always runs on Klein."})
    if (_WORKFLOWS_DIR / "KREA2_TURBO_T2I.json").exists():
        out.append({
            "key": "krea2_turbo", "name": "Krea 2 Turbo", "supports_refs": False,
            "max_refs": 0, "supports_lora": True,
            "note": "Natural prose prompts, no tag spam. Renders on the Krea 2 box "
                    f"({_krea2_host()}) and can load a trained character LoRA — "
                    "include the character's trigger in the prompt (e.g. "
                    "'rbmnredv1 woman …') and name the outfit."})
    return out


# ── pose scaffolds (affirmative, framing-first) ──────────────────────────────
POSE_SCAFFOLDS: Dict[str, str] = {
    "fullbody_front": ("Full body visible from head to feet, standing upright facing "
                       "the camera, arms relaxed at the sides, feet shoulder-width "
                       "apart, plain light gray studio background, soft even lighting."),
    "apose": ("Full body visible from head to feet, standing in a relaxed A-pose facing "
              "the camera, arms held slightly away from the body, plain light gray "
              "studio background, soft even lighting."),
    "tpose": ("Full body visible from head to feet, standing facing the camera with "
              "both arms extended straight out horizontally in a T-pose, plain light "
              "gray studio background, flat even lighting."),
    "portrait": ("Head-and-shoulders portrait facing the camera, plain light gray "
                 "studio background, soft even lighting."),
    "none": "",
}


# ── storage helpers ──────────────────────────────────────────────────────────
def _fdir(slug: str) -> Path:
    from backend.api.klein3 import _cdir
    return _cdir(slug) / "forge"


def _fjson(slug: str) -> Path:
    return _fdir(slug) / "forge.json"


def _fload(slug: str) -> dict:
    fp = _fjson(slug)
    if fp.exists():
        try:
            return json.loads(fp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"images": [], "prompt_history": []}


def _fsave(slug: str, f: dict) -> None:
    d = _fdir(slug)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "forge.json.tmp"
    tmp.write_text(json.dumps(f, indent=2), "utf-8")
    tmp.replace(_fjson(slug))


_FLOCK = threading.Lock()


def _now() -> str:
    from backend.api.klein3 import _now as k3now
    return k3now()


# ── Krea 2 direct-host rendering (the exam-validated path) ───────────────────
def _jget(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _jpost(url: str, body: dict, timeout: float = 60.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _krea2_models(host: str, folder: str) -> List[str]:
    try:
        out = _jget(f"http://{host}:8188/models/{folder}", timeout=15)
        return out if isinstance(out, list) else []
    except Exception:  # noqa: BLE001
        return []


def _krea2_unet(host: str, default: str) -> str:
    """fp8 on 40xx boxes; mxfp8 is Blackwell-only. Prefer fp8 when it exists."""
    if host in _UNET_CACHE:
        return _UNET_CACHE[host]
    unets = _krea2_models(host, "diffusion_models") or _krea2_models(host, "unet")
    turbo = [u for u in unets if "krea2" in u.lower() and "turbo" in u.lower()]
    fp8 = [u for u in turbo if "fp8" in u.lower() and "mxfp8" not in u.lower()]
    pick = fp8[0] if fp8 else (default if (not unets or default in unets)
                               else (turbo[0] if turbo else default))
    _UNET_CACHE[host] = pick
    return pick


def _krea2_core_graph(host: str, prompt: str, w: int, h: int, seed: int,
                      lora: Optional[str], strength: float) -> dict:
    """The tested KREA2 workflow stripped to CORE nodes (the Krea 2 box has no
    decorator custom nodes), with an optional LoraLoaderModelOnly — exactly the
    graph the TURBO exam validated."""
    from backend.api.klein2 import _WORKFLOWS_DIR
    wf = json.loads((_WORKFLOWS_DIR / "KREA2_TURBO_T2I.json").read_text("utf-8"))
    g = copy.deepcopy(wf)
    g["78:15"]["inputs"]["text"] = prompt
    for dead in ("143", "78:72", "63", "82", "141"):
        g.pop(dead, None)
    g["78:75"]["inputs"]["positive"] = ["78:15", 0]
    g["12"]["inputs"]["images"] = ["78:74", 0]
    g["78:76"]["inputs"].update(width=w, height=h, batch_size=1)
    g["78:75"]["inputs"]["seed"] = seed
    g["54"]["inputs"]["unet_name"] = _krea2_unet(host, g["54"]["inputs"]["unet_name"])
    if lora:
        g["200"] = {"inputs": {"lora_name": lora, "strength_model": strength,
                               "model": ["54", 0]},
                    "class_type": "LoraLoaderModelOnly",
                    "_meta": {"title": "character LoRA"}}
        g["78:75"]["inputs"]["model"] = ["200", 0]
    else:
        g["78:75"]["inputs"]["model"] = ["54", 0]
    return g


def _krea2_render(host: str, graph: dict, timeout: float = 420.0) -> bytes:
    base = f"http://{host}:8188"
    r = _jpost(f"{base}/prompt", {"prompt": graph})
    if r.get("error") or r.get("node_errors"):
        raise RuntimeError("Krea2 box rejected the graph: "
                           + json.dumps({k: r.get(k) for k in ("error", "node_errors")})[:500])
    pid = r.get("prompt_id")
    if not pid:
        raise RuntimeError("Krea2 box returned no prompt_id")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2.0)
        try:
            h = _jget(f"{base}/history/{pid}", timeout=15).get(pid)
        except Exception:  # noqa: BLE001
            continue
        if not h:
            continue
        st = h.get("status") or {}
        if st.get("status_str") == "error":
            raise RuntimeError(f"render error: {json.dumps(st.get('messages'))[:400]}")
        imgs = [i for o in (h.get("outputs") or {}).values()
                for i in (o.get("images") or []) if i.get("type") != "temp"]
        if imgs:
            i = imgs[-1]
            url = (f"{base}/view?filename={urllib.parse.quote(i['filename'])}"
                   f"&subfolder={urllib.parse.quote(i.get('subfolder') or '')}"
                   f"&type={i.get('type') or 'output'}")
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
    raise TimeoutError("Krea2 box timed out")


# ── Klein rendering (fans across app workers) ────────────────────────────────
def _render_one_klein(client, prompt: str, refs: List[str],
                      w: int, h: int, seed: int) -> bytes:
    from backend.api.klein2 import (_klein_t2i_graph, _run_prompt_blocking,
                                    _images_from_outputs)
    if refs:
        from backend.api.klein3 import _run_klein_edit_on
        return _run_klein_edit_on(client, prompt, refs, w, h, seed)
    wf = _klein_t2i_graph(prompt, w, h, seed)
    outputs = _run_prompt_blocking(client, wf, 420.0)
    imgs = _images_from_outputs(outputs)
    if not imgs:
        raise RuntimeError("worker produced no image")
    pick = imgs[-1]
    return client.download_output(pick["filename"], pick.get("subfolder", ""),
                                  pick.get("type", "output"))


def _fan_out_klein(disp, slug: str, jobs: List[dict], st: dict) -> None:
    """Klein3's proven pattern: one pinned thread per worker, shared queue,
    live per-job status in st['tasks'] (standing rule 1)."""
    import queue as _q
    from backend.api.klein3 import _klein_workers_all, _short_worker
    workers = _klein_workers_all(disp)
    if not workers:
        raise RuntimeError("no klein-capable worker online")
    qq: Any = _q.Queue()
    for jb in jobs:
        qq.put(jb)
    tasks = st.setdefault("tasks", {})
    for jb in jobs:
        tasks[jb["key"]] = {"worker": None, "status": "queued", "error": None}
    st["workers"] = [_short_worker(u) for u, _c in workers]
    lock = threading.Lock()

    def _loop(url, client):
        sw = _short_worker(url)
        while True:
            try:
                jb = qq.get_nowait()
            except _q.Empty:
                return
            t = tasks[jb["key"]]
            t.update({"worker": sw, "status": "running"})
            try:
                data = _render_one_klein(client, jb["prompt"], jb.get("refs") or [],
                                         jb["w"], jb["h"], jb["seed"])
                with lock:
                    _record_image(slug, data, jb, st)
                t["status"] = "done"
            except Exception as e:  # noqa: BLE001
                t.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
                logger.warning("forge job %r on %s failed: %s", jb["key"], sw, e)

    threads = [threading.Thread(target=_loop, args=wc, daemon=True) for wc in workers]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


def _krea2_hosts_for(lora: Optional[str], disp: Any = None) -> List[str]:
    """Every box that can run THIS Krea 2 job, not just the pinned one.

    ⚠⚠ v1.276.45 — this lane rendered SERIALLY ON ONE BOX and it was habit, not
    hardware. `costumes.py` proved in v1.276.31 that all three workers have
    `krea2_turbo_fp8.safetensors`, and `_krea2_core_graph(host, …)` /
    `_krea2_render(host, …)` have always taken a host. An 8-image batch was
    using a third of the fleet for three times as long.

    **The one genuine pin is a LoRA.** A character LoRA is installed per box, so
    a job that names one may only go where that file exists — asked of each
    candidate directly rather than assumed. A no-LoRA job has no such
    constraint. Falling back to the pinned host is always safe.
    """
    pinned = _krea2_host()
    hosts: List[str] = []
    try:
        from backend.api.klein3 import _klein_workers_all
        for url, _client in _klein_workers_all(disp):
            bare = str(url).replace("http://", "").replace("https://", "")
            h = bare.split(":")[0]
            if h and h not in hosts:
                hosts.append(h)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("forge: krea2 worker list failed (%s) — using the pin", e)
    if not hosts:
        return [pinned]
    if lora:
        # ⚠ MEASURED PER BOX, not assumed. Installing a LoRA is a per-worker
        # action; sending a job to a box without the file is a guaranteed error
        # that looks like a bad render.
        ok = []
        for h in hosts:
            try:
                files = {str(f).replace("\\", "/").split("/")[-1].lower()
                         for f in (_krea2_models(h, "loras") or [])}
                if str(lora).replace("\\", "/").split("/")[-1].lower() in files:
                    ok.append(h)
            except Exception:                                # noqa: BLE001
                continue
        if ok:
            return ok
        logger.info("forge: LoRA %s found on no worker — falling back to %s",
                    lora, pinned)
        return [pinned]
    return hosts


def _run_krea2_jobs(slug: str, jobs: List[dict], st: dict,
                    disp: Any = None) -> None:
    """Krea 2 batch, FANNED across every box that can run it (v1.276.45).

    Round-robin assigned UP FRONT, exactly as `costumes.py` does. ⚠ Not by
    asking the dispatcher inside each thread: `select_worker` sorts on
    `in_flight`, and these lanes submit straight to the client rather than
    through `submit_job`, so `in_flight` is permanently 0 and every concurrent
    caller is handed the SAME box. Round-robin is the only thing that actually
    spreads the work here.
    """
    lora = next((jb.get("lora") for jb in jobs if jb.get("lora")), None)
    hosts = _krea2_hosts_for(lora, disp)
    tasks = st.setdefault("tasks", {})
    for i, jb in enumerate(jobs):
        tasks[jb["key"]] = {"worker": hosts[i % len(hosts)], "status": "queued",
                            "error": None}
    st["workers"] = list(hosts)
    st["krea2_fanned"] = len(hosts) > 1
    if lora and len(hosts) == 1:
        st["krea2_note"] = (f"pinned to {hosts[0]} — LoRA {lora} is installed "
                            f"there and a job cannot run where its LoRA is not")

    def _one(i: int, jb: dict, host: str) -> None:
        t = tasks[jb["key"]]
        t["status"] = "running"
        try:
            g = _krea2_core_graph(host, jb["prompt"], jb["w"], jb["h"], jb["seed"],
                                  jb.get("lora"), jb.get("lora_strength", 1.0))
            data = _krea2_render(host, g)
            with _FLOCK:
                pass
            _record_image(slug, data, jb, st)
            t["status"] = "done"
        except Exception as e:  # noqa: BLE001
            t.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
            logger.warning("forge krea2 job %r on %s failed: %s", jb["key"], host, e)

    if len(hosts) == 1:
        for i, jb in enumerate(jobs):            # nothing to gain from threads
            _one(i, jb, hosts[0])
        return
    threads = [threading.Thread(target=_one, args=(i, jb, hosts[i % len(hosts)]),
                                daemon=True)
               for i, jb in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _record_image(slug: str, data: bytes, jb: dict, st: dict) -> None:
    iid = uuid4().hex[:12]
    d = _fdir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{iid}.png").write_bytes(data)
    rec = {"id": iid, "kind": jb.get("kind", "gen"), "engine": jb.get("engine"),
           "prompt": jb["prompt"], "instruction": jb.get("instruction"),
           "parent": jb.get("parent"), "seed": jb["seed"],
           "width": jb["w"], "height": jb["h"], "pose": jb.get("pose"),
           "lora": jb.get("lora"), "lora_strength": jb.get("lora_strength"),
           "starred": False, "created_at": _now()}
    with _FLOCK:
        f = _fload(slug)
        f["images"].append(rec)
        _fsave(slug, f)
    st.setdefault("images", []).append({"id": iid, "seed": jb["seed"]})
    st["done"] = len(st["images"])


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _img_url(slug: str, iid: str) -> str:
    return f"/api/forge/characters/{slug}/images/{iid}"


def _pub_img(slug: str, rec: dict) -> dict:
    return {**rec, "url": _img_url(slug, rec["id"])}


# ── characters ───────────────────────────────────────────────────────────────
@router.get("/engines")
async def engines():
    return {"engines": _engines_available(), "poses": list(POSE_SCAFFOLDS.keys()),
            "krea2_host": _krea2_host()}


class HostIn(BaseModel):
    host: str


@router.put("/krea2-host")
async def krea2_host_put(body: HostIn):
    host = body.host.strip().replace("http://", "").split(":")[0]
    if not host:
        raise HTTPException(400, "host required")
    s = _fsettings()
    s["krea2_host"] = host
    _fsettings_save(s)
    _UNET_CACHE.pop(host, None)
    return {"ok": True, "krea2_host": host}


def _lora_triggers(files: List[str]) -> Dict[str, str]:
    """Map an installed LoRA filename back to its dataset's trigger phrase.

    Our trained files are named from the dataset id (redv1-bca382-000036) or a
    dest_name that keeps the character prefix (redv1-v2-e21). Match the longest
    dataset-id prefix first, then the id's slug part before the hash — and say
    nothing for files we did not train (his other 36 LoRAs have no trigger)."""
    from backend.api.lora import _DS_ROOT
    cands = []                        # (match_prefix, trigger_phrase)
    if _DS_ROOT.exists():
        for dj in _DS_ROOT.glob("*/dataset.json"):
            try:
                ds = json.loads(dj.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            trig = " ".join(x for x in (ds.get("trigger"), ds.get("class_token")) if x)
            if not trig:
                continue
            ds_id = str(ds.get("id") or "")
            if ds_id:
                cands.append((ds_id.lower(), trig))
                slug = ds_id.rsplit("-", 1)[0]          # redv1-bca382 -> redv1
                if slug:
                    cands.append((slug.lower() + "-", trig))
            # ⚠⚠ v1.276.49 — ALSO match the CHARACTER slug. The installed file is
            # named `<char_slug>-<stamp>-e<epoch>.safetensors`, so matching only
            # on the DATASET id works right up until the dataset is not named
            # after the character — and ⚡ Autogen names every dataset
            # `<slug>-auto`, whose slug-part is `viv2-auto`, which a file called
            # `viv2-0812…` does not start with.
            # Result: his freshly trained LoRA appeared in the list with NO
            # TRIGGER, i.e. installed but unusable, because the trigger word is
            # the whole point. Lowest priority so a dataset-id match still wins.
            cs = str(ds.get("char_slug") or "").strip().lower()
            if cs:
                cands.append((cs + "-", trig))
    cands.sort(key=lambda c: -len(c[0]))                # longest prefix wins
    out: Dict[str, str] = {}
    for f in files:
        base = f.replace("\\", "/").split("/")[-1].lower()
        for prefix, trig in cands:
            if base.startswith(prefix):
                out[f] = trig
                break
    return out


@router.get("/loras")
async def loras():
    """Character LoRAs installed on the Krea 2 box, straight from its own list."""
    host = _krea2_host()
    try:
        files = await asyncio.to_thread(_krea2_models, host, "loras")
    except Exception:  # noqa: BLE001
        files = []
    if not files:
        return {"host": host, "loras": [], "triggers": {},
                "note": f"Krea 2 box at {host}:8188 unreachable or has no LoRAs — "
                        "check the IP (it moves) and that ComfyUI is running."}
    safes = sorted(f for f in files if f.lower().endswith(".safetensors"))
    return {"host": host, "loras": safes, "triggers": _lora_triggers(safes)}


@router.get("/characters")
async def characters():
    from backend.api.klein3 import _K3_ROOT, _load, _public_char
    out = []
    if _K3_ROOT.exists():
        for d in sorted(_K3_ROOT.iterdir()):
            if not (d / "char.json").exists():
                continue
            slug = d.name
            try:
                c = _load(slug)
            except Exception:  # noqa: BLE001
                continue
            pub = _public_char(slug, c)
            f = _fload(slug)
            lore = c.get("lore") or {}
            out.append({"slug": slug, "name": pub["name"], "ref_count": pub["ref_count"],
                        "has_base": pub["has_base"],
                        "has_front": "front" not in pub["missing_views"],
                        "forge_images": len(f.get("images", [])),
                        "lore_filled": bool(lore.get("description") or lore.get("backstory")),
                        "updated_at": pub.get("updated_at")})
    return {"characters": out}


class CharIn(BaseModel):
    name: str


@router.post("/characters")
async def char_create(body: CharIn):
    """Name-first: the character exists (and is resumable) before any render."""
    from backend.api.klein3 import _slugify, _cdir, _save, _load, _public_char
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    slug = _slugify(name)
    if (_cdir(slug) / "char.json").exists():
        c = _load(slug)                      # resume, never clobber
        return {**_public_char(slug, c), "resumed": True}
    c = {"name": name, "fields": {}, "refs": [],
         "base": {"versions": [], "active": None}, "created_at": _now()}
    _save(slug, c)
    _fsave(slug, {"images": [], "prompt_history": []})
    return {**_public_char(slug, c), "resumed": False}


# ── generation ───────────────────────────────────────────────────────────────
class GenIn(BaseModel):
    engine: str = "klein"
    prompt: str
    count: int = 4
    pose: str = "fullbody_front"
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    ref_image_ids: List[str] = []        # forge gallery ids used as references
    use_fields: bool = True              # prepend the character's field sheet
    lora_name: Optional[str] = None      # Krea 2 only — a trained character LoRA
    lora_strength: float = 1.0


def _fields_clause(c: dict) -> str:
    from backend.api.klein3 import _FIELD_ORDER
    bits = []
    fields = c.get("fields") or {}
    for k in _FIELD_ORDER:
        v = str(fields.get(k) or "").strip()
        if v:
            bits.append(f"{k.replace('_', ' ')}: {v}")
    return ("A character with " + "; ".join(bits) + ". ") if bits else ""


def _compose_gen_prompt(c: dict, body: GenIn) -> str:
    parts = []
    if body.use_fields:
        parts.append(_fields_clause(c))
    parts.append(body.prompt.strip())
    scaffold = POSE_SCAFFOLDS.get(body.pose, "")
    if scaffold:
        parts.append(scaffold)
    return " ".join(p for p in parts if p).strip()


@router.post("/characters/{slug}/generate")
async def gen(slug: str, body: GenIn, request: Request):
    from backend.api.klein3 import _load
    c = _load(slug)
    engines_ = {e["key"]: e for e in _engines_available()}
    if body.engine not in engines_:
        raise HTTPException(400, f"engine must be one of {sorted(engines_)}")
    if slug in _RUNS and _RUNS[slug].get("status") == "running":
        raise HTTPException(409, "a run is already in progress for this character")
    if body.lora_name and not engines_[body.engine].get("supports_lora"):
        raise HTTPException(400, "character LoRAs only work with the Krea 2 Turbo engine — "
                                 "that is the model they are trained for")
    refs: List[str] = []
    if body.ref_image_ids:
        if not engines_[body.engine]["supports_refs"]:
            raise HTTPException(400, f"{body.engine} does not accept reference images")
        for iid in body.ref_image_ids[:engines_[body.engine]["max_refs"]]:
            fp = _fdir(slug) / f"{iid}.png"
            if fp.exists():
                refs.append(str(fp))
    prompt = _compose_gen_prompt(c, body)
    count = max(1, min(int(body.count or 1), 8))
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)

    from backend.api.klein3 import _dispatcher
    disp = _dispatcher(request)
    st = {"status": "running", "kind": "gen", "engine": body.engine, "prompt": prompt,
          "lora": body.lora_name, "total": count, "done": 0, "images": [],
          "error": None, "started_at": _now()}
    _RUNS[slug] = st
    with _FLOCK:
        f = _fload(slug)
        hist = f.setdefault("prompt_history", [])
        if body.prompt.strip() and body.prompt.strip() not in hist:
            hist.append(body.prompt.strip())
            del hist[:-30]
        _fsave(slug, f)

    jobs = [{"key": str(i), "kind": "gen", "engine": body.engine, "prompt": prompt,
             "refs": refs, "w": w, "h": h, "seed": base_seed + i, "pose": body.pose,
             "lora": body.lora_name if body.engine == "krea2_turbo" else None,
             "lora_strength": body.lora_strength} for i in range(count)]

    def _run():
        try:
            if body.engine == "krea2_turbo":
                # `disp` so the batch can fan across every box that has the
                # model, rather than queueing on the pinned one (v1.276.45).
                _run_krea2_jobs(slug, jobs, st, disp)
            else:
                if disp is None:
                    raise RuntimeError("dispatcher not ready")
                _fan_out_klein(disp, slug, jobs, st)
            errs = [f"#{int(k) + 1}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            st["error"] = "; ".join(errs[-3:]) if errs else None
            st["status"] = "done" if st["images"] else "error"
            if not st["images"] and not st["error"]:
                st["error"] = "all generations failed"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"total": count, "prompt": prompt, "seed": base_seed,
            "engine": body.engine, "lora": body.lora_name}


class EditIn(BaseModel):
    image_id: str
    instruction: str
    count: int = 2
    extra_ref_ids: List[str] = []        # e.g. a face crop to hold identity
    width: Optional[int] = None
    height: Optional[int] = None
    seed: Optional[int] = None


@router.post("/characters/{slug}/edit")
async def edit(slug: str, body: EditIn, request: Request):
    """The iterate loop: Klein edit with the chosen image as reference 1.

    Instruction is wrapped AFFIRMATIVELY (no negatives — Klein injects what you
    name). Children carry parent=image_id so version chains are traceable."""
    from backend.api.klein3 import _load
    _load(slug)                                   # 404 if unknown
    src = _fdir(slug) / f"{body.image_id}.png"
    if not src.exists():
        raise HTTPException(404, "image not found in this character's gallery")
    if slug in _RUNS and _RUNS[slug].get("status") == "running":
        raise HTTPException(409, "a run is already in progress for this character")
    instruction = (body.instruction or "").strip().rstrip(".")
    if not instruction:
        raise HTTPException(400, "instruction required")
    refs = [str(src)]
    for iid in body.extra_ref_ids[:4]:
        fp = _fdir(slug) / f"{iid}.png"
        if fp.exists():
            refs.append(str(fp))
    prompt = (f"The exact same person as image 1 — same face, same identity. "
              f"{instruction}. Everything not mentioned stays identical to image 1: "
              f"same pose, same framing, same background, same lighting.")
    with _FLOCK:
        f = _fload(slug)
        rec0 = next((r for r in f["images"] if r["id"] == body.image_id), None)
    w = int(body.width or (rec0 or {}).get("width") or 832)
    h = int(body.height or (rec0 or {}).get("height") or 1216)
    count = max(1, min(int(body.count or 1), 4))
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)

    from backend.api.klein3 import _dispatcher
    disp = _dispatcher(request)
    if disp is None:
        raise HTTPException(503, "dispatcher not ready")
    st = {"status": "running", "kind": "edit", "engine": "klein", "prompt": prompt,
          "instruction": instruction, "parent": body.image_id,
          "total": count, "done": 0, "images": [], "error": None, "started_at": _now()}
    _RUNS[slug] = st
    jobs = [{"key": str(i), "kind": "edit", "engine": "klein", "prompt": prompt,
             "instruction": instruction, "parent": body.image_id,
             "refs": refs, "w": w, "h": h, "seed": base_seed + i} for i in range(count)]

    def _run():
        try:
            _fan_out_klein(disp, slug, jobs, st)
            errs = [f"#{int(k) + 1}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            st["error"] = "; ".join(errs[-3:]) if errs else None
            st["status"] = "done" if st["images"] else "error"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"total": count, "prompt": prompt, "seed": base_seed}


@router.get("/characters/{slug}/status")
async def status(slug: str):
    st = _RUNS.get(slug)
    return st or {"status": "idle"}


# ── gallery ──────────────────────────────────────────────────────────────────
@router.get("/characters/{slug}/gallery")
async def gallery(slug: str):
    from backend.api.klein3 import _load
    _load(slug)
    f = _fload(slug)
    recs = [r for r in f.get("images", []) if (_fdir(slug) / f"{r['id']}.png").exists()]
    recs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"images": [_pub_img(slug, r) for r in recs],
            "prompt_history": f.get("prompt_history", [])}


@router.get("/characters/{slug}/images/{iid}")
async def image(slug: str, iid: str, download: bool = False):
    if "/" in iid or "\\" in iid or ".." in iid:
        raise HTTPException(400, "bad id")
    fp = _fdir(slug) / f"{iid}.png"
    if not fp.exists():
        raise HTTPException(404, "no such image")
    if download:
        return FileResponse(str(fp), media_type="image/png", filename=f"{slug}_{iid}.png")
    return FileResponse(str(fp), media_type="image/png")


@router.post("/characters/{slug}/images/{iid}/delete")
async def image_delete(slug: str, iid: str):
    fp = _fdir(slug) / f"{iid}.png"
    if fp.exists():
        fp.unlink()
    with _FLOCK:
        f = _fload(slug)
        f["images"] = [r for r in f.get("images", []) if r["id"] != iid]
        _fsave(slug, f)
    return {"ok": True}


class StarIn(BaseModel):
    starred: bool = True


@router.post("/characters/{slug}/images/{iid}/star")
async def image_star(slug: str, iid: str, body: StarIn):
    with _FLOCK:
        f = _fload(slug)
        for r in f.get("images", []):
            if r["id"] == iid:
                r["starred"] = bool(body.starred)
        _fsave(slug, f)
    return {"ok": True}


# ── promote: finished image → front reference (+ base) ──────────────────────
class PromoteIn(BaseModel):
    image_id: str
    also_base: bool = True               # activate as the working base too


@router.post("/characters/{slug}/promote")
async def promote(slug: str, body: PromoteIn):
    from backend.api.klein3 import _load, _save, _cdir
    c = _load(slug)
    src = _fdir(slug) / f"{body.image_id}.png"
    if not src.exists():
        raise HTTPException(404, "image not found")
    rid = uuid4().hex[:12]
    dest = _cdir(slug) / "refs" / f"{rid}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    c.setdefault("refs", []).append({"id": rid, "tag": "front",
                                     "name": f"forge {body.image_id}",
                                     "source": "forge", "created_at": _now()})
    result = {"ref_id": rid, "tag": "front"}
    if body.also_base:
        vid = uuid4().hex[:12]
        bdest = _cdir(slug) / "base" / f"{vid}.png"
        bdest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, bdest)
        base = c.setdefault("base", {"versions": [], "active": None})
        base["versions"].append({"id": vid, "kind": "ref_copy", "source_ref": rid,
                                 "view": "front", "created_at": _now()})
        base["active"] = vid
        result["base_version"] = vid
    _save(slug, c)
    result["next"] = ("Front reference set. Continue in the Create tab: 🧭 generate the "
                      "missing views (back/left/right), then Clothes, Poses, 🎓 LoRA "
                      "dataset and 🪪 Character Sheet all work from here.")
    return result


# ── lore (the Story Builder substrate) ───────────────────────────────────────
LORE_FIELDS = ["description", "backstory", "personality", "motivations",
               "relationships", "voice", "story_role", "occupation",
               "strengths", "flaws", "fears", "arc", "tags", "notes"]


@router.get("/characters/{slug}/lore")
async def lore_get(slug: str):
    from backend.api.klein3 import _load, _public_char
    c = _load(slug)
    lore = c.get("lore") or {}
    return {"lore": {k: lore.get(k, "" if k != "tags" else []) for k in LORE_FIELDS},
            "fields": c.get("fields") or {},
            "character": _public_char(slug, c)}


class LoreIn(BaseModel):
    lore: Dict[str, Any]


@router.put("/characters/{slug}/lore")
async def lore_put(slug: str, body: LoreIn):
    from backend.api.klein3 import _load, _save
    c = _load(slug)
    cur = c.get("lore") or {}
    for k in LORE_FIELDS:
        if k in body.lore:
            cur[k] = body.lore[k]
    c["lore"] = cur
    _save(slug, c)
    return {"ok": True, "lore": cur}


class LoreGenIn(BaseModel):
    direction: str = ""                  # optional steer: "make him a tragic ex-cop"
    overwrite: bool = False              # False = fill only empty fields


_LORE_SYSTEM = (
    "You are a story bible writer for a music-video / narration-video studio. "
    "Given a character's physical field sheet and an optional creative direction, "
    "write concise, usable lore. Respond with ONLY a JSON object with these keys: "
    "description (2-3 sentences, present tense), backstory (4-6 sentences), "
    "personality (2-3 sentences), motivations (1-2 sentences), relationships "
    "(1-2 sentences), voice (one sentence: how they speak), story_role (a few words, "
    "e.g. 'reluctant protagonist'), occupation (a few words), strengths (comma list), "
    "flaws (comma list), fears (comma list), arc (2 sentences: where they start and "
    "where they could end), tags (array of 5-10 short lowercase strings). "
    "No markdown, no commentary — JSON only.")


@router.post("/characters/{slug}/lore/generate")
async def lore_generate(slug: str, body: LoreGenIn,
                        session: AsyncSession = Depends(get_session)):
    from backend.api.klein3 import _load, _save, _active_base_path
    from backend.api.character_studio import _ollama_chat_json, _app_settings
    c = _load(slug)
    s = await _app_settings(session)
    urls = (getattr(s, "ollama_urls", None) or
            ([s.ollama_base_url] if getattr(s, "ollama_base_url", None) else []))
    model = getattr(s, "ollama_model", None)
    if not urls or not model:
        raise HTTPException(409, "Ollama is not configured (Settings → LLM) — fill the "
                                 "lore manually or configure ollama_urls + ollama_model.")
    fields = json.dumps(c.get("fields") or {}, indent=0)
    existing = json.dumps({k: v for k, v in (c.get("lore") or {}).items() if v}, indent=0)
    direction = body.direction or "(none — your choice, grounded in the physical sheet)"
    user = (f"Character name: {c.get('name', slug)}\nPhysical fields: {fields}\n"
            f"Existing lore (keep consistent with it): {existing}\n"
            f"Creative direction: {direction}")
    img_b64 = None
    ab = _active_base_path(slug, c)
    if ab and ab.exists():
        img_b64 = base64.b64encode(ab.read_bytes()).decode("ascii")

    content = await asyncio.to_thread(
        _ollama_chat_json, urls, model, _LORE_SYSTEM, user, img_b64)
    if not content:
        raise HTTPException(502, "LLM did not answer — check the Ollama server")
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    try:
        parsed = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        raise HTTPException(502, f"LLM output was not JSON. Raw: {raw[:300]}")
    cur = c.get("lore") or {}
    changed = []
    for k in LORE_FIELDS:
        if k not in parsed:
            continue
        if body.overwrite or not cur.get(k):
            cur[k] = parsed[k]
            changed.append(k)
    c["lore"] = cur
    _save(slug, c)
    return {"ok": True, "lore": cur, "changed": changed}


@router.get("/studio-overview")
async def studio_overview():
    """🏠 The studio hub: every character's whole pipeline at a glance."""
    from backend.api.klein3 import _K3_ROOT, _load, _public_char
    from backend.api.lora import _DS_ROOT
    from backend.api.charsheet import _ROOT as sheets_root
    # ⚠ cfg.project_dir is OVERRIDDEN from the DB after import — modules capture
    # their roots at import time, so derive from those, never from cfg at
    # request time (that mismatch made this route count 0 sheets, v1.272.1).
    train_dir = _DS_ROOT.parent / "_train"
    auto_dir = _DS_ROOT.parent / "_autogen"
    # legacy installed LoRAs (trained via scripts, no _train state): match the
    # box's own lora list by dataset-id/slug prefix, cached 60s.
    box_loras: List[str] = []
    now = time.time()
    if _OV_CACHE.get("t", 0) > now - 60:
        box_loras = _OV_CACHE.get("loras", [])
    else:
        try:
            box_loras = [f for f in _krea2_models(_krea2_host(), "loras")
                         if f.lower().endswith(".safetensors")]
        except Exception:  # noqa: BLE001
            box_loras = []
        _OV_CACHE.update(t=now, loras=box_loras)

    # datasets + training state, grouped by character ------------------------
    ds_by_char: Dict[str, list] = {}
    if _DS_ROOT.exists():
        for dj in _DS_ROOT.glob("*/dataset.json"):
            try:
                ds = json.loads(dj.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items = ds.get("items", [])
            rec = {"id": ds.get("id"), "total": len(items),
                   "rendered": sum(1 for i in items if i.get("status") == "done"),
                   "flagged": sum(1 for i in items
                                  if (i.get("qc") or {}).get("ok") is False),
                   "trigger": " ".join(x for x in (ds.get("trigger"),
                                                   ds.get("class_token")) if x)}
            tr = train_dir / f"{ds.get('id')}.json"
            if tr.exists():
                try:
                    t = json.loads(tr.read_text("utf-8"))
                    rec["train_stage"] = t.get("stage")
                    rec["installed_lora"] = t.get("installed")
                except Exception:  # noqa: BLE001
                    pass
            ds_by_char.setdefault(ds.get("char_slug") or "", []).append(rec)

    out = []
    if _K3_ROOT.exists():
        for d in sorted(_K3_ROOT.iterdir()):
            if not (d / "char.json").exists():
                continue
            slug = d.name
            try:
                c = _load(slug)
            except Exception:  # noqa: BLE001
                continue
            pub = _public_char(slug, c)
            lore = c.get("lore") or {}
            f = _fload(slug)
            dss = ds_by_char.get(slug, [])
            auto = {}
            aj = auto_dir / f"{slug}.json"
            if aj.exists():
                try:
                    a = json.loads(aj.read_text("utf-8"))
                    auto = {"stage": a.get("stage"), "detail": a.get("detail")}
                except Exception:  # noqa: BLE001
                    pass
            sheet_count = (len(list((sheets_root / slug).glob("sheet_*.png")))
                           if (sheets_root / slug).exists() else 0)
            installed = [x["installed_lora"] for x in dss if x.get("installed_lora")]
            prefixes = [str(x.get("id") or "").lower() for x in dss] + [slug.lower() + "-"]
            for bl in box_loras:
                base = bl.replace("\\", "/").split("/")[-1].lower()
                if any(pref and base.startswith(pref) for pref in prefixes) \
                        and bl not in installed:
                    installed.append(bl)
            out.append({
                "slug": slug, "name": pub["name"],
                "thumb": pub.get("active_base_url"),
                "has_front": "front" not in pub["missing_views"],
                "missing_views": pub["missing_views"],
                "ref_count": pub["ref_count"], "has_base": pub["has_base"],
                "forge_images": len(f.get("images", [])),
                "datasets": dss, "installed_loras": installed,
                "autogen": auto, "sheets": sheet_count,
                "lore_filled": bool(lore.get("description") or lore.get("backstory")),
                "updated_at": pub.get("updated_at"),
            })
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"characters": out}


@router.get("/health")
async def health():
    return {"ok": True, "engines": [e["key"] for e in _engines_available()],
            "poses": list(POSE_SCAFFOLDS.keys()), "lore_fields": LORE_FIELDS,
            "krea2_host": _krea2_host()}
