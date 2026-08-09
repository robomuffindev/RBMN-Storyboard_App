"""Klein 3.0 — pure Klein reference mode (v1.201.0).

No 3D anywhere.  The character is a set of tagged 2D REFERENCE images and a
single ACTIVE BASE image; poses are plain IMAGES (shared Pose Library 2.0);
generation is the simplest possible Klein multi-ref edit:

    image 1 = the character's active base (identity)
    image 2 = the pose image
    prompt  = "the person from image 1 in the exact pose from image 2"

Everything here reuses machinery already proven elsewhere in the app:
  - reference upload + tagging            (Klein create flow concept, stored here)
  - vision analyze -> description fields  (frontend calls the existing
                                           /api/studio/vnccs wizard endpoints)
  - missing-view synthesis                (Klein N-ref edit, per-view prompts)
  - strip to underwear / nude             (Klein 1-ref edit — the base-render
                                           'strip' recipe, applied to any ref)
  - GAN upscale                           (STUDIO_UPSCALE.json +
                                           prepare_studio_upscale_workflow)
  - pose library                          (Klein 2.0's store, same endpoints)
  - generation batches + saved refs       (Klein 2.0 pattern)

Storage: <project_dir>/_libraries/klein3/chars/<slug>/
    char.json                    {name, fields, refs[], base{versions[],active}}
    refs/<id>.png                reference images (tag lives in char.json)
    base/<id>.png                base versions (ref-copy / stripped / upscaled)
    _gen/_gen_<gid>/             generation batches (status.json + refs + N.png)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings as cfg
from backend.services.comfyui.workflow import (
    prepare_klein_workflow, prepare_studio_upscale_workflow,
)
from backend.api.klein2 import (          # shared, already-proven helpers
    _WORKFLOWS_DIR, _klein_worker, _run_prompt_blocking, _images_from_outputs,
    _read_poses, _K2_POSES, _pose_desc, _clean_pose_desc,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/klein3", tags=["klein3"])

_K3_ROOT = Path(cfg.project_dir) / "_libraries" / "klein3" / "chars"
_BG_TASKS: set = set()
_JOBS: Dict[str, dict] = {}      # f"{slug}:{kind}" -> {"status","detail","error"}

REF_TAGS = ["front", "back", "left", "right", "face", "outfit", "other"]
VIEW_TAGS = ["front", "back", "left", "right"]

_FIELD_ORDER = ["age", "sex", "race", "skin_color", "hair", "eyes", "face",
                "body", "height", "aesthetics", "additional_details"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:48] or uuid4().hex[:8]


def _cdir(slug: str) -> Path:
    if "/" in slug or "\\" in slug or ".." in slug:
        raise HTTPException(400, "bad slug")
    return _K3_ROOT / slug


def _load(slug: str) -> dict:
    fp = _cdir(slug) / "char.json"
    if not fp.exists():
        raise HTTPException(404, f"character {slug!r} not found")
    try:
        return json.loads(fp.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(500, f"char.json unreadable: {e}")


def _save(slug: str, c: dict) -> None:
    c["updated_at"] = _now()
    d = _cdir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "char.json").write_text(json.dumps(c, indent=2), "utf-8")


def _save_png_bytes(raw: bytes, dest: Path) -> None:
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")


def _ref_by_id(c: dict, rid: str) -> Optional[dict]:
    return next((r for r in c.get("refs", []) if r.get("id") == rid), None)


def _refs_by_tag(c: dict, tag: str) -> List[dict]:
    return [r for r in c.get("refs", []) if r.get("tag") == tag]


def _active_base_path(slug: str, c: dict) -> Optional[Path]:
    """Explicit active base version, else the front-tagged reference (his
    spec: the front image is the default base)."""
    base = c.get("base") or {}
    active = base.get("active")
    if active:
        p = _cdir(slug) / "base" / f"{active}.png"
        if p.exists():
            return p
    fronts = _refs_by_tag(c, "front")
    if fronts:
        p = _cdir(slug) / "refs" / f"{fronts[-1]['id']}.png"
        if p.exists():
            return p
    return None


# ── Base MODE (v1.217) ───────────────────────────────────────────────────────
# "stripped" is a choice, not a stage.  Stripping costs an extra Klein edit per
# view AND introduces its own drift, so when a shot does not need the clothing
# replaced, the uploaded reference (or a generated missing view, which lands as
# a tagged ref) is the better identity image.
_BASE_MODES = ("auto", "dressed", "stripped")


def _ver_dressed(v: dict) -> Optional[bool]:
    """True = clothed, False = stripped, None = genuinely unknown.

    None is not a failure mode to paper over: base versions written before
    v1.217 recorded no provenance on an upscale, so claiming either way would be
    a guess.  Callers rank known matches first and fall back to unknown rather
    than dropping a whole tier — the same lesson as the v1.205 `ups or vers` bug,
    where an empty preferred tier skipped every candidate behind it."""
    kind = str(v.get("kind") or "")
    if kind.startswith("stripped_"):
        return False
    if kind == "ref_copy":
        return True
    if kind == "upscaled":
        from_kind = str(v.get("from_kind") or "")
        if from_kind.startswith("stripped_"):
            return False
        if from_kind:
            return True
        return None                      # pre-v1.217 upscale: unknowable
    return None


def _base_mode(c: dict, override: Optional[str] = None) -> str:
    """Per-request override wins, else the character's default, else auto."""
    for cand in (override, ((c.get("base") or {}).get("mode"))):
        m = str(cand or "").strip().lower()
        if m in _BASE_MODES:
            return m
    return "auto"


def _base_for_view(slug: str, c: dict, view: str,
                   mode: Optional[str] = None) -> Tuple[Optional[Path], str]:
    """Identity image for a pose's DOMINANT ANGLE (v1.205, mode-aware v1.217).

    Priority: an UPSCALED base version of that view -> any base version of that
    view -> a reference image tagged with that view -> the active base.  The
    second value LABELS which source won, so the job line, the gallery and the
    log all say which identity actually ran (never infer the code path).

    `mode` filters that list:
      dressed  -- clothed sources only.  Skips stripped versions and upscales OF
                  stripped versions; the tagged-reference tier is inherently
                  dressed, so a character with no dressed base still works off
                  his uploads and generated views with no strip run at all.
      stripped -- prefers stripped versions and upscales of them.
      auto     -- pre-v1.217 behaviour: newest of that view wins."""
    view = (view or "").strip().lower()
    mode = _base_mode(c, mode)
    want = {"dressed": True, "stripped": False}.get(mode)
    if view in VIEW_TAGS:
        vers = [v for v in ((c.get("base") or {}).get("versions") or [])
                if (v.get("view") or "") == view]
        ups = [v for v in vers if v.get("kind") == "upscaled"]
        # upscaled first (newest), then the rest of that view — a missing file
        # must fall through to the next candidate, not skip the whole tier
        ordered = list(reversed(ups)) + list(reversed([v for v in vers if v not in ups]))
        if want is not None:
            # exact matches first, then unknown-provenance, then never the
            # opposite kind — a dressed run must not silently use a nude base.
            ordered = ([v for v in ordered if _ver_dressed(v) is want]
                       + [v for v in ordered if _ver_dressed(v) is None])
        for pick in ordered:
            fp = _cdir(slug) / "base" / f"{pick['id']}.png"
            if fp.exists():
                known = _ver_dressed(pick)
                tag = "" if known is want or want is None else " · provenance unknown"
                return fp, f"{view} base ({pick.get('kind', 'base')}{tag})"
        for r in reversed(_refs_by_tag(c, view)):
            fp = _cdir(slug) / "refs" / f"{r['id']}.png"
            if fp.exists():
                # A reference is always clothed. In stripped mode that is a
                # fallback, not the request — say so instead of implying a strip.
                note = " · dressed fallback" if mode == "stripped" else ""
                gen = " (generated)" if r.get("source") == "generated" else ""
                return fp, f"{view} reference{gen}{note}"
    fp = _active_base_path(slug, c)
    if not fp:
        return None, "none"
    return fp, ("active base" if not view else f"active base (no {view} view yet)")


def _identity_ref_paths(slug: str, c: dict, limit: int = 3) -> List[str]:
    """Best identity refs for view synthesis: front first, then face, then
    newest others."""
    ordered: List[dict] = []
    for tag in ("front", "face"):
        ordered += _refs_by_tag(c, tag)
    ordered += [r for r in c.get("refs", []) if r not in ordered]
    out: List[str] = []
    for r in ordered:
        p = _cdir(slug) / "refs" / f"{r['id']}.png"
        if p.exists():
            out.append(str(p))
        if len(out) >= limit:
            break
    return out


def _public_char(slug: str, c: dict, full: bool = False) -> dict:
    base = c.get("base") or {"versions": [], "active": None}
    ab = _active_base_path(slug, c)
    out = {
        "slug": slug, "name": c.get("name", slug),
        "fields": c.get("fields", {}),
        "ref_count": len(c.get("refs", [])),
        "has_base": ab is not None,
        "active_base_url": f"/api/klein3/characters/{slug}/base/active/image" if ab else None,
        "missing_views": [v for v in VIEW_TAGS if not _refs_by_tag(c, v)],
        "updated_at": c.get("updated_at"),
    }
    if full:
        out["refs"] = [{"id": r["id"], "tag": r.get("tag", "other"),
                        "name": r.get("name", ""), "source": r.get("source", "upload"),
                        "created_at": r.get("created_at"),
                        "url": f"/api/klein3/characters/{slug}/refs/{r['id']}/image"}
                       for r in c.get("refs", [])]
        out["base_versions"] = [{**v, "url": f"/api/klein3/characters/{slug}/base/{v['id']}/image"}
                                for v in base.get("versions", [])]
        out["active_base"] = base.get("active")
        out["base_mode"] = _base_mode(c)
        out["base_sources"] = {v: _base_for_view(slug, c, v)[1] for v in VIEW_TAGS}
        out["jobs"] = {k.split(":", 1)[1]: v for k, v in _JOBS.items()
                       if k.startswith(slug + ":")}
    return out


def _dispatcher(request: Request):
    return getattr(request.app.state, "comfy_dispatcher", None)


def _short_worker(url: str) -> str:
    return str(url).replace("http://", "").replace("https://", "").rstrip("/")


def _klein_workers_all(disp) -> List[tuple]:
    """ALL healthy klein-capable (non-runpod) workers as (url, client) — the
    fan-out pool.  Falls back to the single select_worker pick."""
    out: List[tuple] = []
    try:
        for w in (getattr(disp, "workers", {}) or {}).values():
            if not getattr(w, "healthy", False) or getattr(w, "is_runpod", False):
                continue
            caps = getattr(w, "capabilities", set()) or set()
            if "klein" in caps:
                cl = disp.clients.get(w.url)
                if cl:
                    out.append((w.url, cl))
    except Exception:  # noqa: BLE001
        pass
    if not out:
        wk, cl = _klein_worker(disp)
        if cl:
            out.append((getattr(wk, "url", "worker"), cl))
    return out


def _run_klein_edit_on(client, prompt: str, ref_paths: List[str],
                       w: int, h: int, seed: int, timeout: float = 420.0) -> bytes:
    """One Klein N-ref edit on a GIVEN worker client; returns image bytes."""
    names: List[str] = []
    for rp in ref_paths[:5]:
        up = f"k3_ref_{uuid4().hex[:8]}.png"
        client.upload_image(rp, up)
        names.append(up)
    n = max(1, min(len(names), 5))
    path = _WORKFLOWS_DIR / f"KLEIN_EDIT_ULTRA_WORKFLOW_{n}REF.json"
    if not path.exists():
        raise FileNotFoundError(f"workflow {path.name} not found")
    wf = prepare_klein_workflow(str(path), prompt, w, h, seed, ref_images=names[:n])
    outputs = _run_prompt_blocking(client, wf, timeout)
    imgs = _images_from_outputs(outputs)
    if not imgs:
        raise RuntimeError("worker produced no image")
    pick = imgs[-1]
    return client.download_output(pick["filename"], pick.get("subfolder", ""),
                                  pick.get("type", "output"))


def _run_klein_edit_sync(disp, prompt: str, ref_paths: List[str],
                         w: int, h: int, seed: int, timeout: float = 420.0,
                         st: Optional[dict] = None) -> bytes:
    """Single Klein edit; records WHICH worker ran it into ``st['worker']``."""
    workers = _klein_workers_all(disp)
    if not workers:
        raise RuntimeError("no klein-capable worker online")
    url, client = workers[0]
    if st is not None:
        st["worker"] = _short_worker(url)
    return _run_klein_edit_on(client, prompt, ref_paths, w, h, seed, timeout)


def _parallel_klein_edits(disp, jobs: List[dict], on_result, st: dict) -> None:
    """Fan a list of Klein edit jobs out across ALL klein workers: one pinned
    thread per worker pulls from a shared queue (true threading, per Lorenzo's
    standing rule).  Live status per job in ``st['tasks'][key]`` =
    {worker, status queued|running|done|error, error} — the UI polls this.
    ``on_result(job, bytes)`` runs under a lock (safe char.json/status writes)."""
    import queue as _q
    import threading
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
                data = _run_klein_edit_on(client, jb["prompt"], jb["refs"],
                                          jb["w"], jb["h"], jb["seed"])
                with lock:
                    on_result(jb, data)
                t["status"] = "done"
            except Exception as e:  # noqa: BLE001
                t.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
                logger.warning("klein3 parallel job %r on %s failed: %s", jb["key"], sw, e)

    threads = [threading.Thread(target=_loop, args=wc, daemon=True)
               for wc in workers]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


def _job(slug: str, kind: str) -> dict:
    return _JOBS.setdefault(f"{slug}:{kind}", {})


def _spawn(fn) -> None:
    task = asyncio.create_task(asyncio.to_thread(fn))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


# ── Characters ───────────────────────────────────────────────────────────────
@router.get("/characters")
async def characters():
    out = []
    if _K3_ROOT.exists():
        for d in sorted(_K3_ROOT.iterdir()):
            if (d / "char.json").exists():
                try:
                    out.append(_public_char(d.name, json.loads((d / "char.json").read_text("utf-8"))))
                except Exception:
                    continue
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"characters": out, "field_order": _FIELD_ORDER, "ref_tags": REF_TAGS}


class CharIn(BaseModel):
    name: str


@router.post("/characters")
async def char_create(body: CharIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    slug = _slugify(name)
    if (_cdir(slug) / "char.json").exists():
        raise HTTPException(409, f"character {slug!r} already exists")
    c = {"name": name, "fields": {}, "refs": [],
         "base": {"versions": [], "active": None}, "created_at": _now()}
    _save(slug, c)
    return _public_char(slug, c, full=True)


@router.post("/characters/{slug}/delete")
async def char_delete(slug: str):
    d = _cdir(slug)
    if not (d / "char.json").exists():
        raise HTTPException(404, "not found")
    shutil.rmtree(d, ignore_errors=True)
    for k in list(_JOBS):
        if k.startswith(slug + ":"):
            _JOBS.pop(k, None)
    return {"deleted": slug}


@router.get("/characters/{slug}")
async def char_get(slug: str):
    return _public_char(slug, _load(slug), full=True)


class FieldsIn(BaseModel):
    fields: Dict[str, Any]


@router.post("/characters/{slug}/fields")
async def char_fields(slug: str, body: FieldsIn):
    c = _load(slug)
    clean = {k: str(v).strip() for k, v in (body.fields or {}).items()
             if k in _FIELD_ORDER and str(v).strip()}
    c["fields"] = clean
    _save(slug, c)
    return {"fields": clean}


# ── References (upload / tag / delete / serve) ───────────────────────────────
@router.post("/characters/{slug}/refs")
async def ref_upload(slug: str, file: UploadFile = File(...), tag: str = Form("other")):
    raw = await file.read()          # await BEFORE _load: the load→append→save
    if not raw:                      # section then runs without yields, so
        raise HTTPException(400, "empty file")   # concurrent uploads can't clobber
    c = _load(slug)
    rid = uuid4().hex[:12]
    try:
        _save_png_bytes(raw, _cdir(slug) / "refs" / f"{rid}.png")
    except Exception as e:
        raise HTTPException(400, f"unreadable image: {e}")
    rec = {"id": rid, "tag": tag if tag in REF_TAGS else "other",
           "name": file.filename or f"{rid}.png", "source": "upload",
           "created_at": _now()}
    c.setdefault("refs", []).append(rec)
    _save(slug, c)
    return {**rec, "url": f"/api/klein3/characters/{slug}/refs/{rid}/image"}


class RefUpdateIn(BaseModel):
    tag: str


@router.post("/characters/{slug}/refs/{rid}/update")
async def ref_update(slug: str, rid: str, body: RefUpdateIn):
    c = _load(slug)
    r = _ref_by_id(c, rid)
    if not r:
        raise HTTPException(404, "reference not found")
    if body.tag not in REF_TAGS:
        raise HTTPException(400, f"tag must be one of {', '.join(REF_TAGS)}")
    r["tag"] = body.tag
    _save(slug, c)
    return {"id": rid, "tag": r["tag"]}


@router.post("/characters/{slug}/refs/{rid}/delete")
async def ref_delete(slug: str, rid: str):
    c = _load(slug)
    r = _ref_by_id(c, rid)
    if not r:
        raise HTTPException(404, "reference not found")
    c["refs"] = [x for x in c["refs"] if x["id"] != rid]
    try:
        (_cdir(slug) / "refs" / f"{rid}.png").unlink(missing_ok=True)
    except Exception:
        pass
    _save(slug, c)
    return {"deleted": rid}


@router.get("/characters/{slug}/refs/{rid}/image")
async def ref_image(slug: str, rid: str):
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    p = _cdir(slug) / "refs" / f"{rid}.png"
    if not p.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(p), media_type="image/png")


# ── Missing-view synthesis (Klein N-ref edit, per-view prompt) ───────────────
_VIEW_PROMPTS = {
    "front": ("seen directly from the FRONT, facing the camera"),
    "back": ("seen directly from BEHIND — a full back view showing the back of "
             "the head, hairstyle and outfit from behind"),
    "left": ("seen in a full LEFT-SIDE profile view, facing to the viewer's left"),
    "right": ("seen in a full RIGHT-SIDE profile view, facing to the viewer's right"),
}


def _view_prompt(view: str, fields: Dict[str, Any]) -> str:
    extra = ", ".join(str(fields.get(k, "")).strip() for k in ("hair", "body")
                      if str(fields.get(k, "")).strip())
    return (f"The exact same person shown in the reference image(s), full body "
            f"shot {_VIEW_PROMPTS[view]}, standing straight with arms relaxed at "
            f"the sides, SAME face, SAME hairstyle, SAME outfit and SAME body "
            f"proportions as the references{', ' + extra if extra else ''}, "
            f"plain white studio background, even lighting, photorealistic")


def _face_prompt(fields: Dict[str, Any]) -> str:
    """The 🙂 face anchor: a zoomed close-up rendered BEFORE the view set, then
    fed to every view job as reference image 1 so faces match across the set
    (Lorenzo, 2026-08-09: generated sets drifted on the face without it)."""
    extra = ", ".join(str(fields.get(k, "")).strip() for k in ("hair", "eyes", "face")
                      if str(fields.get(k, "")).strip())
    return ("The exact same person shown in the reference image(s), a zoomed-in "
            "close-up PORTRAIT of the face — head and shoulders only, the face "
            "filling most of the frame, looking straight at the camera with a "
            "neutral expression, SAME face, SAME eyes, SAME hairstyle and SAME "
            f"skin tone as the references{', ' + extra if extra else ''}, sharp "
            "focus on the eyes and facial features, plain white studio "
            "background, even lighting, photorealistic")


class ViewsIn(BaseModel):
    views: List[str]
    seed: Optional[int] = None
    width: int = 832
    height: int = 1216
    face_first: bool = True            # 🙂 render/reuse a face close-up anchor
    regen_face: bool = False           # force a fresh face even if one exists


@router.post("/characters/{slug}/views/generate")
async def views_generate(slug: str, body: ViewsIn, request: Request):
    c = _load(slug)
    todo = [v for v in (body.views or []) if v in VIEW_TAGS]
    if not todo:
        raise HTTPException(400, f"views must be from {', '.join(VIEW_TAGS)}")
    id_refs = _identity_ref_paths(slug, c)
    if not id_refs:
        raise HTTPException(409, "upload at least one reference first")
    st = _job(slug, "views")
    if st.get("status") == "running":
        raise HTTPException(409, "a view-generation job is already running")
    disp = _dispatcher(request)
    seed0 = int(body.seed) if body.seed else random.randint(1, 2_000_000_000)
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    fields = c.get("fields", {})
    st.clear()
    st.update({"status": "running", "detail": f"0/{len(todo)}", "error": None, "done": []})

    def _run():
        # ── 🙂 phase 1: the face anchor ──────────────────────────────────────
        # A zoomed face close-up is generated FIRST (or the newest existing
        # face-tagged ref reused), then leads the reference list of every view
        # job — the strongest identity signal Klein gets, so the set's faces
        # match. Failure falls back to the plain identity refs, never blocks.
        refs_for_views = list(id_refs)
        total = len(todo)
        if body.face_first:
            face_path: List[str] = []
            existing = [r for r in _refs_by_tag(c, "face")
                        if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
            if existing and not body.regen_face:
                face_path = [str(_cdir(slug) / "refs" / f"{existing[-1]['id']}.png")]
            else:
                total += 1
                st["detail"] = f"0/{total} (face anchor first)"
                fjob = [{"key": "face", "prompt": _face_prompt(fields),
                         "refs": id_refs, "w": 832, "h": 1024, "seed": seed0 - 1}]

                def on_face(jb, data):
                    rid = uuid4().hex[:12]
                    p = _cdir(slug) / "refs" / f"{rid}.png"
                    _save_png_bytes(data, p)
                    c2 = _load(slug)
                    c2.setdefault("refs", []).append(
                        {"id": rid, "tag": "face",
                         "name": "generated face close-up (anchor)",
                         "source": "generated", "created_at": _now()})
                    _save(slug, c2)
                    face_path.append(str(p))
                    st["done"] = st.get("done", []) + ["face"]
                    st["detail"] = f"{len(st['done'])}/{total}"

                try:
                    _parallel_klein_edits(disp, fjob, on_face, st)
                except Exception as e:  # noqa: BLE001
                    logger.warning("klein3 face anchor failed, views proceed "
                                   "without it: %s", e)
            if face_path:
                refs_for_views = ([face_path[0]] +
                                  [p for p in id_refs if p != face_path[0]])[:3]

        # ── phase 2: the views, face-anchored ───────────────────────────────
        jobs = [{"key": v, "prompt": _view_prompt(v, fields),
                 "refs": refs_for_views, "w": w, "h": h, "seed": seed0 + i}
                for i, v in enumerate(todo)]

        def on_result(jb, data):
            rid = uuid4().hex[:12]
            _save_png_bytes(data, _cdir(slug) / "refs" / f"{rid}.png")
            c2 = _load(slug)
            c2.setdefault("refs", []).append(
                {"id": rid, "tag": jb["key"], "name": f"generated {jb['key']} view",
                 "source": "generated", "created_at": _now()})
            _save(slug, c2)
            st["done"] = st.get("done", []) + [jb["key"]]
            st["detail"] = f"{len(st['done'])}/{total}"

        try:
            _parallel_klein_edits(disp, jobs, on_result, st)   # fans across workers
            errs = [f"{k}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            st["error"] = "; ".join(errs) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "views": todo}


# ── Strip (underwear / nude base from any reference) ─────────────────────────
_STRIP_MODES = ("underwear", "nude")


def _strip_prompt(mode: str, fields: Dict[str, Any]) -> str:
    """Explicitly name every garment class to remove — 'every other piece of
    clothing removed' left shirts on (measured 2026-08-03).  Sex-aware so
    'underwear' means the right garments."""
    sex = str(fields.get("sex") or "").strip().lower()
    if mode == "underwear":
        under = ("a plain fitted gray sports bra and plain gray briefs"
                 if sex.startswith(("f", "w")) else "plain fitted gray boxer briefs")
        wear = (f"now wearing ONLY {under} and NOTHING else — the shirt, t-shirt, top, "
                "jacket and EVERY piece of upper-body clothing is completely REMOVED, "
                "showing bare skin on the chest, stomach, back and arms; all pants, "
                "shorts, skirts and other lower-body clothing removed except the underwear")
    else:
        wear = ("now completely NUDE, wearing NOTHING at all — the shirt, top, jacket, "
                "pants and every single piece of clothing is removed, bare skin over "
                "the entire body")
    return ("The exact same person from image 1 — identical face, identical hairstyle, "
            "identical body and identical standing pose, camera angle and framing — "
            f"{wear}, and completely BAREFOOT: no shoes, no sandals, no boots, no "
            "socks, and no accessories. Plain white studio background, even lighting, "
            "photorealistic full body shot.")


class StripIn(BaseModel):
    mode: str = "underwear"            # 'underwear' | 'nude'
    source_ref_id: Optional[str] = None  # strip just that ref
    view: Optional[str] = None         # strip just that VIEW's slot (🔁 on a version)
    seed: Optional[int] = None
    width: int = 832
    height: int = 1216


@router.post("/characters/{slug}/strip")
async def strip(slug: str, body: StripIn, request: Request):
    """Strip references into base versions.  Default (no source_ref_id): the
    FULL SET — newest ref of each view tag (front/back/left/right) stripped in
    PARALLEL across workers, so the whole standing set is ready as stripped
    reference material; the front result auto-activates and any version can be
    activated by click for pose generation.  With source_ref_id: just that ref."""
    c = _load(slug)
    mode = body.mode if body.mode in _STRIP_MODES else "underwear"
    sources: List[tuple] = []          # (label, path)
    if body.source_ref_id:
        src = _ref_by_id(c, body.source_ref_id)
        if not src:
            raise HTTPException(404, "source reference not found")
        p = _cdir(slug) / "refs" / f"{src['id']}.png"
        if not p.exists():
            raise HTTPException(409, "source image missing on disk")
        sources = [(src.get("tag") or "ref", p)]
    elif body.view:
        if body.view not in VIEW_TAGS:
            raise HTTPException(400, f"view must be one of {', '.join(VIEW_TAGS)}")
        rs = _refs_by_tag(c, body.view)
        if not rs:
            raise HTTPException(409, f"no reference tagged {body.view!r}")
        p = _cdir(slug) / "refs" / f"{rs[-1]['id']}.png"
        if not p.exists():
            raise HTTPException(409, "source image missing on disk")
        sources = [(body.view, p)]
    else:
        for v in VIEW_TAGS:
            rs = _refs_by_tag(c, v)
            if rs:
                p = _cdir(slug) / "refs" / f"{rs[-1]['id']}.png"
                if p.exists():
                    sources.append((v, p))
        if not sources:
            raise HTTPException(409, "no view-tagged references (front/back/left/right) "
                                "— tag them, generate missing views, or pick a single source")
    st = _job(slug, "strip")
    if st.get("status") == "running":
        raise HTTPException(409, "a strip job is already running")
    disp = _dispatcher(request)
    seed = int(body.seed) if body.seed else random.randint(1, 2_000_000_000)
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    prompt = _strip_prompt(mode, c.get("fields", {}))
    st.clear()
    st.update({"status": "running", "detail": f"{mode} ×{len(sources)}", "error": None})

    def _run():
        jobs = [{"key": lbl, "prompt": prompt, "refs": [str(p)],
                 "w": w, "h": h, "seed": seed + i}
                for i, (lbl, p) in enumerate(sources)]
        made: Dict[str, str] = {}

        def on_result(jb, data):
            vid = uuid4().hex[:12]
            _save_png_bytes(data, _cdir(slug) / "base" / f"{vid}.png")
            c2 = _load(slug)
            base = c2.setdefault("base", {"versions": [], "active": None})
            new_rec = {"id": vid, "kind": f"stripped_{mode}", "view": jb["key"],
                       "seed": jb["seed"], "created_at": _now()}
            # SET semantics: each view holds ONE stripped slot — a regenerate
            # REPLACES it in place instead of growing the version list.
            replaced = None
            for idx, v in enumerate(base["versions"]):
                if v.get("view") == jb["key"] and str(v.get("kind", "")).startswith("stripped_"):
                    replaced = v
                    base["versions"][idx] = new_rec
                    break
            if replaced is None:
                base["versions"].append(new_rec)
            else:
                if base.get("active") == replaced["id"]:
                    base["active"] = vid          # active follows its slot
                try:
                    (_cdir(slug) / "base" / f"{replaced['id']}.png").unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
            made[jb["key"]] = vid
            _save(slug, c2)

        try:
            _parallel_klein_edits(disp, jobs, on_result, st)   # fans across workers
            errs = [f"{k}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            # full-set runs: front stripped = default base. Single-view 🔁 runs
            # keep the current active (slot replacement already re-pointed it).
            pick = None if (body.view or body.source_ref_id) else (
                made.get("front") or (next(iter(made.values())) if made else None))
            if pick:
                c2 = _load(slug)
                c2.setdefault("base", {"versions": [], "active": None})["active"] = pick
                _save(slug, c2)
            st["error"] = "; ".join(errs) if errs else None
            st["status"] = ("done" if made and not errs
                            else "done_with_errors" if made else "error")
        except Exception as e:  # noqa: BLE001
            logger.warning("klein3 strip[%s] failed: %s", slug, e)
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "mode": mode, "count": len(sources), "seed": seed}


# ── Base versions (activate / from-ref / upscale / serve) ────────────────────
class ActivateIn(BaseModel):
    version_id: str


@router.post("/characters/{slug}/base/activate")
async def base_activate(slug: str, body: ActivateIn):
    c = _load(slug)
    base = c.setdefault("base", {"versions": [], "active": None})
    if not any(v["id"] == body.version_id for v in base["versions"]):
        raise HTTPException(404, "version not found")
    base["active"] = body.version_id
    _save(slug, c)
    return {"active": body.version_id}


class FromRefIn(BaseModel):
    ref_id: str


@router.post("/characters/{slug}/base/from_ref")
async def base_from_ref(slug: str, body: FromRefIn):
    """Promote a reference image to a base version (and activate it)."""
    c = _load(slug)
    r = _ref_by_id(c, body.ref_id)
    if not r:
        raise HTTPException(404, "reference not found")
    src = _cdir(slug) / "refs" / f"{r['id']}.png"
    if not src.exists():
        raise HTTPException(409, "reference image missing on disk")
    vid = uuid4().hex[:12]
    dest = _cdir(slug) / "base" / f"{vid}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    base = c.setdefault("base", {"versions": [], "active": None})
    # v1.217 BUG FIX: the view was never recorded, so `_base_for_view` — which
    # filters on `(v.get("view") or "") == view` — could never match a ref copy
    # to an angle.  It was reachable only as the active base, which is precisely
    # the "use my uploaded reference instead of a stripped one" path.
    base["versions"].append({"id": vid, "kind": "ref_copy", "source_ref": r["id"],
                             "view": str(r.get("tag") or "").strip().lower(),
                             "created_at": _now()})
    base["active"] = vid
    _save(slug, c)
    return {"active": vid}


class BaseModeIn(BaseModel):
    mode: str = "auto"               # auto | dressed | stripped


@router.put("/characters/{slug}/base-mode")
async def base_mode_set(slug: str, body: BaseModeIn):
    """The character's DEFAULT identity source.  `dressed` means his own clothes
    from the references; nothing has to be stripped for him to be usable."""
    mode = str(body.mode or "").strip().lower()
    if mode not in _BASE_MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(_BASE_MODES)}")
    c = _load(slug)
    c.setdefault("base", {"versions": [], "active": None})["mode"] = mode
    _save(slug, c)
    # Show what each view WOULD resolve to under this mode — the point of the
    # toggle is that he can see the consequence before spending a render.
    resolved = {}
    for v in VIEW_TAGS:
        fp, label = _base_for_view(slug, c, v, mode)
        resolved[v] = {"found": bool(fp), "source": label}
    return {"mode": mode, "resolves_to": resolved}


class UpscaleIn(BaseModel):
    model_name: Optional[str] = None   # worker GAN model; default from workflow


@router.post("/characters/{slug}/base/upscale")
async def base_upscale(slug: str, body: UpscaleIn, request: Request):
    """GAN-upscale the ACTIVE base via the proven STUDIO_UPSCALE graph; the
    upscaled result becomes the new active base (his spec)."""
    c = _load(slug)
    src = _active_base_path(slug, c)
    if not src:
        raise HTTPException(409, "no active base yet")
    wf_path = _WORKFLOWS_DIR / "STUDIO_UPSCALE.json"
    if not wf_path.exists():
        raise HTTPException(500, "workflow STUDIO_UPSCALE.json not found")
    st = _job(slug, "upscale")
    if st.get("status") == "running":
        raise HTTPException(409, "an upscale job is already running")
    disp = _dispatcher(request)
    model_name = body.model_name
    st.clear()
    st.update({"status": "running", "detail": "upscale", "error": None})

    def _run():
        try:
            _wk, client = _klein_worker(disp)
            if not client:
                raise RuntimeError("no worker online")
            st["worker"] = _short_worker(getattr(_wk, "url", "worker"))
            up = f"k3_up_{uuid4().hex[:8]}.png"
            client.upload_image(str(src), up)
            wf = prepare_studio_upscale_workflow(str(wf_path), image_path=up,
                                                 model_name=model_name)
            outputs = _run_prompt_blocking(client, wf, 300)
            imgs = _images_from_outputs(outputs)
            if not imgs:
                raise RuntimeError("worker produced no image")
            pick = imgs[-1]
            data = client.download_output(pick["filename"], pick.get("subfolder", ""),
                                          pick.get("type", "output"))
            vid = uuid4().hex[:12]
            (_cdir(slug) / "base").mkdir(parents=True, exist_ok=True)
            (_cdir(slug) / "base" / f"{vid}.png").write_bytes(data)
            c2 = _load(slug)
            base = c2.setdefault("base", {"versions": [], "active": None})
            # keep the view label so angle matching still works after upscaling
            _act = base.get("active")
            _src = next((v for v in base.get("versions", [])
                         if v.get("id") == _act), {})
            _src_view = _src.get("view", "")
            # v1.217 BUG FIX: the record kept the view but not what it was
            # upscaled FROM — and `_base_for_view` prefers upscaled first, so a
            # dressed run would happily pick an upscale of a stripped image.
            base["versions"].append({"id": vid, "kind": "upscaled", "view": _src_view,
                                     "from_kind": str(_src.get("kind") or ""),
                                     "from_id": _act,
                                     "created_at": _now()})
            base["active"] = vid          # upscaled becomes the active version
            _save(slug, c2)
            st.update({"status": "done", "version": vid})
        except Exception as e:  # noqa: BLE001
            logger.warning("klein3 upscale[%s] failed: %s", slug, e)
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True}


@router.post("/characters/{slug}/base/{vid}/delete")
async def base_delete(slug: str, vid: str):
    if "/" in vid or "\\" in vid or ".." in vid:
        raise HTTPException(400, "bad id")
    c = _load(slug)
    base = c.setdefault("base", {"versions": [], "active": None})
    if not any(v["id"] == vid for v in base["versions"]):
        raise HTTPException(404, "version not found")
    base["versions"] = [v for v in base["versions"] if v["id"] != vid]
    if base.get("active") == vid:
        base["active"] = None       # falls back to the front-tagged ref
    try:
        (_cdir(slug) / "base" / f"{vid}.png").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    _save(slug, c)
    return {"deleted": vid, "active": base.get("active")}


@router.get("/characters/{slug}/base/{vid}/image")
async def base_image(slug: str, vid: str):
    if "/" in vid or "\\" in vid or ".." in vid:
        raise HTTPException(400, "bad id")
    c = _load(slug)
    if vid == "active":
        p = _active_base_path(slug, c)
        if not p:
            raise HTTPException(404, "no active base")
        return FileResponse(str(p), media_type="image/png")
    p = _cdir(slug) / "base" / f"{vid}.png"
    if not p.exists():
        raise HTTPException(404, "version not found")
    return FileResponse(str(p), media_type="image/png")


@router.get("/characters/{slug}/jobs")
async def jobs(slug: str):
    _load(slug)      # 404 on unknown slug
    return {k.split(":", 1)[1]: v for k, v in _JOBS.items() if k.startswith(slug + ":")}


# ── Generation: base + pose -> character in pose ─────────────────────────────
# v1.208: every clause is AFFIRMATIVE.  This graph has no negative-prompt node
# and runs at cfg=1 (see KLEIN_EDIT_ULTRA_WORKFLOW_2REF: CFGGuider cfg 1, negative
# wired to empty conditioning) — "do NOT make him thinner" has nothing behind it
# and simply feeds "thinner" to the text encoder.  State what SHOULD be true.
# The exclusion also NAMES image 2's body attributes, per the named-objects rule:
# "appearance / style" are category words and get ignored.
_GEN_PROMPT = (
    "The person from image 1, standing in the body pose shown in image 2. "
    "Everything about him comes from image 1: his face, his hairstyle, his skin, "
    "his clothing, his build, his weight, his height, his limb thickness and his "
    "proportions are the ones in image 1. Image 2 supplies the POSE only — the "
    "joint angles, the direction each arm and leg points, and which way the body "
    "faces. Image 2's own build, weight, height and limb thickness belong to "
    "image 2 alone; his body is the body in image 1. Photorealistic, "
    "natural lighting, full body shot, plain neutral background."
)
# The pose in words — BRIEF is the default (v1.207): the long paragraph pushed the
# identity clauses away from the end of the prompt, and the body drifted.
_POSE_TEXT_BRIEF = " The pose, in words: {desc}."
# v1.208.1: CONTACT beats geometry.  Image 2's arms are as long as image 2's
# body is wide; on a wider body the same arm angle puts the hand on the belly.
# Ships with brief AND full so it is always present when the pose is described.
_POSE_CONTACT = (
    " Where the pose puts a hand or a foot on the body, it lands on the named body part of HIS "
    "body: hands on the hips settle on his own hip bones at the sides of his waist, level with "
    "the top of his pelvis, with the fingers wrapping toward his back. His arms reach as far "
    "as they need to and his elbows swing as wide as they need to for his own width — the "
    "contact point is what matters, and the arm angle follows it."
)
_POSE_TEXT_FULL = (
    " Image 2 is a diagram of the pose: read the joint angles from it and land every hand, "
    "arm, foot and knee on the matching part of HIS body — hands on the hips rest on his own "
    "hip bones at the sides of his own waist, a hand on the thigh rests on his own thigh. "
    "The balance and the facing come from image 2."
)
# TERMINAL clause (last when on).  v1.208: stated POSITIVELY — the v1.207 wording
# was a list of "do NOT" guards, which on a cfg=1 graph with no negative
# conditioning just injected "thinner / taller / more athletic" into the prompt.
_BODY_LOCK = (
    " The body in the result is the body from image 1: the same weight, the same width at "
    "the shoulders, the chest, the belly, the waist and the hips, the same limb thickness, "
    "the same stature and the same head-to-body proportion. His arms, his legs, his torso "
    "angle and his head direction are the only things that move to form the pose."
)
_BOOST_NOTE = (
    " Image 3 shows the SAME person as image 1 from another view: use image 1 and image 3 "
    "together for his face, his hair and his body, and image 2 for the pose."
)


def _body_words(c: dict) -> str:
    """The character's OWN build in words, from his description fields — naming
    the build holds it better than 'same as image 1' alone."""
    f = c.get("fields") or {}
    bits = []
    for key, lead in (("body", "his build is"), ("height", "his height is")):
        v = str(f.get(key) or "").strip().rstrip(".")
        if v:
            bits.append(f"{lead} {v}")
    return f" Remember his physique: {'; '.join(bits)}." if bits else ""


def _compose_prompt(c: dict, pose: dict, pose_text: str = "brief", body_lock: bool = True,
                    body_words: bool = True, boosted: bool = False, extra: str = "",
                    bodyfit: bool = False) -> str:
    """THE single prompt builder — used by /generate, /generate-set and the
    zero-cost /preview-prompt endpoint, so what the panel shows is what runs.

    Order matters: identity opener -> (diagram note) -> pose in words -> his own
    build -> the user's extra -> BODY LOCK last (freshest clause wins)."""
    prompt = _GEN_PROMPT
    if bodyfit:
        prompt += _BODYFIT_NOTE
    if boosted:
        prompt += _BOOST_NOTE
    if pose.get("source") == "upload" or not (pose.get("prompt") or "").strip():
        prompt += _POSE_DIAGRAM_NOTE
    mode = (pose_text or "brief").strip().lower()
    # v1.208: build words in the DESCRIPTION pull the render toward that build
    desc = _clean_pose_desc(_pose_desc(pose)) if mode in ("brief", "full") else ""
    if desc:
        prompt += _POSE_TEXT_BRIEF.format(desc=desc.rstrip(" ."))
        prompt += _POSE_CONTACT
        if mode == "full":
            prompt += _POSE_TEXT_FULL
    if body_words:
        prompt += _body_words(c)
    extra = (extra or "").strip()
    if extra:
        prompt = f"{prompt} {extra}"
    if body_lock:
        prompt += _BODY_LOCK
    return prompt


# ── Body-matched pose mannequins (v1.208, his idea) ─────────────────────────
# Reshape the pose mannequin to HIS proportions FIRST, then render against it.
# Image 2 then carries his own build, so there is no competing body to leak.
_POSEFIT_PROMPT = (
    "Image 1 is a plain gray mannequin holding a pose. Image 2 shows a real person. "
    "Redraw the mannequin from image 1 with the body shape of the person in image 2: "
    "the same weight, the same belly, the same width at the shoulders, the chest, the "
    "waist and the hips, the same limb thickness and the same stature as image 2. "
    "The pose stays exactly as it is in image 1 — the same joint angles, the same "
    "direction for every arm and leg, the same facing, the same camera framing. "
    "The result is still a smooth featureless light-gray 3d mannequin: blank face, no "
    "hair, no clothing, matte gray surface, whole body visible head to feet, plain white "
    "seamless background, soft even studio lighting."
)
_BODYFIT_NOTE = (
    " Image 2's mannequin was already shaped to his own proportions, so its body and his "
    "body agree — follow it for the pose."
)


def _posefit_path(slug: str, pose_id: str) -> Path:
    return _cdir(slug) / "posefit" / f"{pose_id}.png"


def _identity_boost_path(slug: str, c: dict, primary: Optional[Path]) -> Optional[Path]:
    """A SECOND image of the same person for image 3: the front base, else a
    face-tagged ref, else the front ref — never the one already used as image 1."""
    cands: List[Path] = []
    for v in reversed(((c.get("base") or {}).get("versions") or [])):
        if (v.get("view") or "") == "front":
            cands.append(_cdir(slug) / "base" / f"{v['id']}.png")
    for tag in ("face", "front"):
        for r in reversed(_refs_by_tag(c, tag)):
            cands.append(_cdir(slug) / "refs" / f"{r['id']}.png")
    for fp in cands:
        if fp.exists() and (primary is None or fp.resolve() != primary.resolve()):
            return fp
    return None
_POSE_DIAGRAM_NOTE = (
    " Image 2 may be a pose diagram — a gray mannequin, an openpose stick-figure "
    "skeleton, or a depth map; read the body pose from it."
)


_GEN_LIVE: Dict[str, dict] = {}     # gid -> live status (worker/task detail while running)


def _gen_dir(gid: str) -> Path:
    return _K3_ROOT.parent / "_gen" / f"_gen_{gid}"


def _read_gen(gid: str) -> Optional[dict]:
    fp = _gen_dir(gid) / "status.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text("utf-8"))
    except Exception:
        return None


def _write_gen(gid: str, st: dict) -> None:
    d = _gen_dir(gid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(st), "utf-8")


class GenerateIn(BaseModel):
    slug: str
    pose_id: str
    prompt_extra: str = ""
    count: int = 2
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity
    describe_pose: bool = True       # legacy switch (False == pose_text "off")
    pose_text: str = "brief"         # off | brief | full — how much pose wording
    body_lock: bool = True           # terminal "do not slim/stretch him" clause
    body_words: bool = True          # inject his own build/height words
    identity_boost: bool = False     # add a 2nd identity image as image 3
    pose_source: str = "library"     # library | bodyfit (his body-matched mannequin)
    base_mode: Optional[str] = None  # auto | dressed | stripped (None = character default)


@router.post("/generate")
async def generate(body: GenerateIn, request: Request):
    c = _load(body.slug)
    base = _active_base_path(body.slug, c)
    if not base:
        raise HTTPException(409, "no base yet — tag a front reference or strip one")
    pose = next((it for it in _read_poses() if it.get("id") == body.pose_id), None)
    if pose is None:
        raise HTTPException(404, "pose not found (Pose Library 2.0)")
    pose_fp = _K2_POSES / f"{body.pose_id}.png"
    if not pose_fp.exists():
        raise HTTPException(409, "pose image missing — regenerate it in the library")
    fitted = _posefit_path(body.slug, body.pose_id)
    use_fit = body.pose_source == "bodyfit" and fitted.exists()
    if use_fit:
        pose_fp = fitted

    # v1.205: hand the pose the base view that faces the same way it does.
    pose_view = (pose.get("view") or "") if body.match_angle else ""
    ident, ident_src = _base_for_view(body.slug, c, pose_view, body.base_mode)
    if ident:
        base = ident

    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")

    boost_fp = _identity_boost_path(body.slug, c, base) if body.identity_boost else None
    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    pose_text = _pose_desc(pose) if mode in ("brief", "full") else ""
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost_fp is not None,
                             extra=body.prompt_extra, bodyfit=use_fit)

    count = max(1, min(int(body.count or 1), 8))
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)

    gid = uuid4().hex[:12]
    gd = _gen_dir(gid)
    gd.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base, gd / "ref_identity.png")
    shutil.copy2(pose_fp, gd / "ref_pose.png")
    ref_paths = [str(gd / "ref_identity.png"), str(gd / "ref_pose.png")]
    ref_names = ["ref_identity.png", "ref_pose.png"]
    if boost_fp is not None:                      # image 3 = same person, other view
        shutil.copy2(boost_fp, gd / "ref_identity2.png")
        ref_paths.append(str(gd / "ref_identity2.png"))
        ref_names.append("ref_identity2.png")
    st = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
          "pose": pose.get("name"), "pose_id": body.pose_id, "prompt": prompt,
          "pose_view": pose_view, "identity_source": ident_src, "pose_desc": pose_text,
          "total": count, "done": 0, "images": [], "error": None,
          "width": w, "height": h, "refs": ref_names,
          "identity_boost": boost_fp is not None, "pose_text_mode": mode,
          "pose_source": "bodyfit" if use_fit else "library",
          "body_lock": body.body_lock, "body_words": body.body_words,
          "created_at": _now()}
    _write_gen(gid, st)

    _GEN_LIVE[gid] = st          # live view (worker/task detail) while running

    def _run():
        jobs = [{"key": str(i), "prompt": prompt, "refs": ref_paths,
                 "w": w, "h": h, "seed": base_seed + i} for i in range(count)]

        def on_result(jb, data):
            nm = f"{jb['key']}.png"
            (gd / nm).write_bytes(data)
            st["images"].append({"id": nm, "seed": jb["seed"]})
            st["done"] = len(st["images"])
            _write_gen(gid, st)

        try:
            _parallel_klein_edits(disp, jobs, on_result, st)   # fans across workers
            errs = [f"#{int(k) + 1}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            st["done"] = count
            st["error"] = "; ".join(errs[-3:]) if errs else None
            st["status"] = "done" if st["images"] else "error"
            if not st["images"] and not st["error"]:
                st["error"] = "all generations failed"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
        _write_gen(gid, st)
        _GEN_LIVE.pop(gid, None)

    _spawn(_run)
    logger.info("klein3 generate[%s]: %s pose=%s view=%s identity=%s count=%d", gid,
                body.slug, pose.get("name"), pose_view or "-", ident_src, count)
    return {"gen_id": gid, "total": count, "prompt": prompt, "seed": base_seed,
            "pose_view": pose_view, "identity_source": ident_src}


class PoseFitIn(BaseModel):
    pose_ids: Optional[List[str]] = None
    category: Optional[str] = None     # …or a whole SET
    tags: Optional[List[str]] = None   # …or a TAG selection
    overwrite: bool = False
    match_angle: bool = True           # shape each pose against ITS angle-matched base


@router.post("/characters/{slug}/posefit")
async def posefit(slug: str, body: PoseFitIn, request: Request):
    """Reshape pose mannequins to THIS character's build (Lorenzo's idea).

    One Klein 2-ref edit per pose — image 1 the mannequin, image 2 his base —
    producing a mannequin with his proportions in the same pose.  Cached under
    the character (`posefit/<pose_id>.png`) and reused by every later run, so
    the cost is once per character+pose.  Fanned across all klein workers with
    per-pose worker/status, per the standing rule."""
    c = _load(slug)
    st = _job(slug, "posefit")
    if st.get("status") == "running":
        raise HTTPException(409, "a pose-fit run is already going for this character")
    poses = _read_poses()
    want = [t.strip() for t in (body.tags or []) if t.strip()]
    ids = set(body.pose_ids or [])
    targets = [it for it in poses
               if (it.get("id") in ids if ids
                   else ((it.get("set") or "Custom") == body.category if body.category
                         else any(t in (it.get("tags") or []) for t in want)))
               and (_K2_POSES / f"{it['id']}.png").exists()]
    if not body.overwrite:
        targets = [it for it in targets if not _posefit_path(slug, it["id"]).exists()]
    if not targets:
        return {"started": False, "note": "every selected pose already has a body-matched "
                                          "mannequin (tick overwrite to redo them)"}
    if len(targets) > 40:
        targets = targets[:40]
    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")
    _posefit_path(slug, "x").parent.mkdir(parents=True, exist_ok=True)
    seed0 = random.randint(1, 2_000_000_000)
    jobs = []
    for i, it in enumerate(targets):
        view = (it.get("view") or "") if body.match_angle else ""
        base, _src = _base_for_view(slug, c, view)
        if not base:
            raise HTTPException(409, "no base yet — tag a front reference or strip one")
        jobs.append({"key": it["id"], "prompt": _POSEFIT_PROMPT,
                     "refs": [str(_K2_POSES / f"{it['id']}.png"), str(base)],
                     "w": 832, "h": 1216, "seed": seed0 + i, "name": it.get("name", "")})
    st.clear()
    st.update({"status": "running", "detail": f"0/{len(jobs)}", "error": None,
               "total": len(jobs)})

    def on_result(jb, data):
        _save_png_bytes(data, _posefit_path(slug, jb["key"]))
        done = sum(1 for t in st.get("tasks", {}).values() if t.get("status") == "done") + 1
        st["detail"] = f"{done}/{len(jobs)}"

    def _run():
        try:
            _parallel_klein_edits(disp, jobs, on_result, st)
            errs = [f"{t.get('error')}" for t in (st.get("tasks", {}) or {}).values()
                    if t.get("status") == "error"]
            st["error"] = "; ".join(errs[:3]) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    logger.info("klein3 posefit[%s]: %d pose(s)", slug, len(jobs))
    return {"started": True, "total": len(jobs)}


@router.get("/characters/{slug}/posefit/{pose_id}/image")
async def posefit_image(slug: str, pose_id: str):
    if "/" in pose_id or "\\" in pose_id or ".." in pose_id:
        raise HTTPException(400, "bad id")
    fp = _posefit_path(slug, pose_id)
    if not fp.exists():
        raise HTTPException(404, "no body-matched mannequin for this pose yet")
    return FileResponse(str(fp), media_type="image/png")


@router.get("/characters/{slug}/posefit")
async def posefit_list(slug: str):
    """Which poses already have a body-matched mannequin (drives the UI counts)."""
    _load(slug)
    d = _cdir(slug) / "posefit"
    ids = sorted(f.stem for f in d.glob("*.png")) if d.exists() else []
    return {"pose_ids": ids, "count": len(ids),
            "job": _JOBS.get(f"{slug}:posefit") or None}


@router.post("/characters/{slug}/posefit/{pose_id}/delete")
async def posefit_delete(slug: str, pose_id: str):
    if "/" in pose_id or "\\" in pose_id or ".." in pose_id:
        raise HTTPException(400, "bad id")
    _posefit_path(slug, pose_id).unlink(missing_ok=True)
    return {"deleted": pose_id}


class PreviewIn(BaseModel):
    slug: str
    pose_id: Optional[str] = None
    category: Optional[str] = None    # preview the first pose of a SET …
    tags: Optional[List[str]] = None  # … or of a TAG selection
    prompt_extra: str = ""
    match_angle: bool = True
    describe_pose: bool = True
    pose_text: str = "brief"
    body_lock: bool = True
    body_words: bool = True
    identity_boost: bool = False
    pose_source: str = "library"


@router.post("/preview-prompt")
async def preview_prompt(body: PreviewIn):
    """The EXACT prompt and reference set a run would use — costs nothing, spends
    no worker time.  Same `_compose_prompt` the generators call, so what the panel
    shows is what runs."""
    c = _load(body.slug)
    poses = _read_poses()
    pose = None
    if body.pose_id:
        pose = next((it for it in poses if it.get("id") == body.pose_id), None)
    else:
        want = [t.strip() for t in (body.tags or []) if t.strip()]
        cand = [it for it in poses
                if ((it.get("set") or "Custom") == body.category if body.category
                    else any(t in (it.get("tags") or []) for t in want))
                and (_K2_POSES / f"{it['id']}.png").exists()]
        pose = cand[0] if cand else None
    if pose is None:
        raise HTTPException(404, "no pose to preview")
    pose_view = (pose.get("view") or "") if body.match_angle else ""
    ident, ident_src = _base_for_view(body.slug, c, pose_view, body.base_mode)
    boost = _identity_boost_path(body.slug, c, ident) if body.identity_boost else None
    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    fitted = _posefit_path(body.slug, pose["id"])
    use_fit = body.pose_source == "bodyfit" and fitted.exists()
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost is not None,
                             extra=body.prompt_extra, bodyfit=use_fit)
    return {"prompt": prompt, "words": len(prompt.split()),
            "pose_source": "bodyfit" if use_fit else "library",
            "pose_desc_clean": _clean_pose_desc(_pose_desc(pose)),
            "pose": pose.get("name"), "pose_id": pose.get("id"),
            "pose_view": pose_view, "identity_source": ident_src,
            "pose_desc": _pose_desc(pose),
            "refs": (["image 1: " + ident_src,
                      "image 2: " + ("body-matched mannequin" if use_fit else "pose")]
                     + (["image 3: second identity view"] if boost else [])),
            "identity_boost": boost is not None}


def _gen_public(gid: str, st: dict) -> dict:
    return {"gen_id": gid, "status": st.get("status"), "done": st.get("done", 0),
            "total": st.get("total", 0), "character": st.get("character"),
            "slug": st.get("slug"), "pose": st.get("pose"), "pose_id": st.get("pose_id"),
            "set": st.get("set"), "pose_view": st.get("pose_view"),
            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),
            "identity_boost": st.get("identity_boost"), "pose_text_mode": st.get("pose_text_mode"),
            "pose_source": st.get("pose_source"),
            "created_at": st.get("created_at"), "prompt": st.get("prompt", ""),
            "images": [{"id": im["id"], "url": f"/api/klein3/gen/{gid}/image/{im['id']}",
                        "seed": im.get("seed")} for im in st.get("images", [])],
            "refs": [{"name": r, "url": f"/api/klein3/gen/{gid}/image/{r}"}
                     for r in st.get("refs", [])],
            "error": st.get("error"), "tasks": st.get("tasks"),
            "workers": st.get("workers")}


class GenerateSetIn(BaseModel):
    slug: str
    category: Optional[str] = None   # the pose SET to run …
    tags: Optional[List[str]] = None  # … or ANY-match TAGS across all sets
    prompt_extra: str = ""
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # per-pose DOMINANT ANGLE identity
    describe_pose: bool = True       # legacy switch (False == pose_text "off")
    pose_text: str = "brief"         # off | brief | full
    body_lock: bool = True
    body_words: bool = True
    identity_boost: bool = False
    pose_source: str = "library"     # library | bodyfit
    base_mode: Optional[str] = None  # auto | dressed | stripped


@router.post("/generate-set")
async def generate_set(body: GenerateSetIn, request: Request):
    """Generate the character in EVERY rendered pose of a set — one gen record
    per pose (each lands in the gallery linked to its pose), fanned across all
    klein workers; live progress via the character's 'set' job."""
    c = _load(body.slug)
    base = _active_base_path(body.slug, c)
    if not base:
        raise HTTPException(409, "no base yet — tag a front reference or strip one")
    want_tags = [t.strip() for t in (body.tags or []) if t.strip()]
    if not body.category and not want_tags:
        raise HTTPException(400, "pass a set (category) or tags")
    label = body.category or ("tags: " + ", ".join(want_tags))
    poses = [it for it in _read_poses()
             if ((it.get("set") or "Custom") == body.category if body.category
                 else any(t in (it.get("tags") or []) for t in want_tags))
             and (_K2_POSES / f"{it['id']}.png").exists()]
    if not poses:
        raise HTTPException(404, f"no rendered poses match {label!r}")
    if len(poses) > 40:
        poses = poses[:40]
    st = _job(body.slug, "set")
    if st.get("status") == "running":
        raise HTTPException(409, "a set generation is already running for this character")
    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")
    seed0 = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    extra = body.prompt_extra.strip()

    # one gen record per pose, visible in the gallery immediately
    gen_map: Dict[str, tuple] = {}
    angle_used: Dict[str, int] = {}
    for i, p in enumerate(poses):
        gid = uuid4().hex[:12]
        gd = _gen_dir(gid)
        gd.mkdir(parents=True, exist_ok=True)
        # v1.205: each pose gets the base view matching ITS dominant angle
        p_view = (p.get("view") or "") if body.match_angle else ""
        p_base, p_src = _base_for_view(body.slug, c, p_view, body.base_mode)
        angle_used[p_src] = angle_used.get(p_src, 0) + 1
        p_boost = (_identity_boost_path(body.slug, c, p_base or base)
                   if body.identity_boost else None)
        shutil.copy2(p_base or base, gd / "ref_identity.png")
        if p_boost is not None:
            shutil.copy2(p_boost, gd / "ref_identity2.png")
        p_fit = _posefit_path(body.slug, p["id"])
        p_use_fit = body.pose_source == "bodyfit" and p_fit.exists()
        shutil.copy2(p_fit if p_use_fit else _K2_POSES / f"{p['id']}.png",
                     gd / "ref_pose.png")
        p_mode = (body.pose_text or "brief") if body.describe_pose else "off"
        p_text = _pose_desc(p) if p_mode in ("brief", "full") else ""
        prompt = _compose_prompt(c, p, pose_text=p_mode, body_lock=body.body_lock,
                                 body_words=body.body_words, boosted=p_boost is not None,
                                 extra=extra, bodyfit=p_use_fit)
        gst = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
               "pose": p.get("name"), "pose_id": p["id"], "set": label,
               "pose_view": p_view, "identity_source": p_src, "pose_desc": p_text,
               "prompt": prompt, "total": 1, "done": 0, "images": [], "error": None,
               "width": w, "height": h,
               "refs": (["ref_identity.png", "ref_pose.png"]
                        + (["ref_identity2.png"] if p_boost is not None else [])),
               "identity_boost": p_boost is not None, "pose_text_mode": p_mode,
               "pose_source": "bodyfit" if p_use_fit else "library",
               "created_at": _now()}
        _write_gen(gid, gst)
        _GEN_LIVE[gid] = gst
        gen_map[p["id"]] = (gid, gd, gst, prompt, seed0 + i)

    st.clear()
    st.update({"status": "running", "detail": f"{label} 0/{len(poses)}",
               "error": None, "set": label, "total": len(poses),
               "identities": angle_used})
    logger.info("klein3 generate-set[%s]: %s poses=%d identities=%s", body.slug, label,
                len(poses), angle_used)

    def _run():
        jobs = [{"key": pid, "prompt": pr,
                 "refs": ([str(gd / "ref_identity.png"), str(gd / "ref_pose.png")]
                          + ([str(gd / "ref_identity2.png")]
                             if (gd / "ref_identity2.png").exists() else [])),
                 "w": w, "h": h, "seed": sd}
                for pid, (gid, gd, gst, pr, sd) in gen_map.items()]

        def on_result(jb, data):
            gid, gd, gst, pr, sd = gen_map[jb["key"]]
            (gd / "0.png").write_bytes(data)
            gst.update({"images": [{"id": "0.png", "seed": jb["seed"]}],
                        "done": 1, "status": "done"})
            _write_gen(gid, gst)
            _GEN_LIVE.pop(gid, None)
            done_n = sum(1 for t in st.get("tasks", {}).values() if t.get("status") == "done") + 1
            st["detail"] = f"{label} {done_n}/{len(jobs)}"

        try:
            _parallel_klein_edits(disp, jobs, on_result, st)
            for pid, (gid, gd, gst, pr, sd) in gen_map.items():
                t = (st.get("tasks", {}) or {}).get(pid) or {}
                if t.get("status") == "error" and gst.get("status") == "running":
                    gst.update({"status": "error", "error": t.get("error"), "done": 1})
                    _write_gen(gid, gst)
                    _GEN_LIVE.pop(gid, None)
            errs = [t.get("error") for t in (st.get("tasks", {}) or {}).values()
                    if t.get("status") == "error" and t.get("error")]
            st["error"] = "; ".join(errs[:3]) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
        except Exception as e:  # noqa: BLE001
            for pid, (gid, gd, gst, pr, sd) in gen_map.items():
                if gst.get("status") == "running":
                    gst.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
                    _write_gen(gid, gst)
                    _GEN_LIVE.pop(gid, None)
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    logger.info("klein3 generate-set[%s]: %s poses=%d", body.slug, label, len(poses))
    return {"started": True, "total": len(poses), "set": label, "seed": seed0}


@router.get("/characters/{slug}/gens")
async def gens_list(slug: str, limit: int = 60):
    """All saved generation batches for this character, newest first — every
    batch stays linked to the pose that made it (pose_id/pose name)."""
    _load(slug)                      # 404 on unknown character
    root = _K3_ROOT.parent / "_gen"
    out: List[dict] = []
    if root.exists():
        for d in root.iterdir():
            if not d.name.startswith("_gen_"):
                continue
            gid = d.name[len("_gen_"):]
            st = _GEN_LIVE.get(gid) or _read_gen(gid)
            if not st or st.get("slug") != slug:
                continue
            out.append(_gen_public(gid, st))
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"gens": out[:max(1, min(limit, 200))]}


@router.post("/gen/{gid}/delete")
async def gen_delete(gid: str):
    if "/" in gid or "\\" in gid or ".." in gid:
        raise HTTPException(400, "bad id")
    d = _gen_dir(gid)
    if not d.exists():
        raise HTTPException(404, "generation not found")
    if _GEN_LIVE.get(gid):
        raise HTTPException(409, "generation still running")
    shutil.rmtree(d, ignore_errors=True)
    return {"deleted": gid}


@router.get("/gen/{gid}")
async def gen_status(gid: str):
    if "/" in gid or "\\" in gid or ".." in gid:
        raise HTTPException(400, "bad id")
    st = _GEN_LIVE.get(gid) or _read_gen(gid)     # live dict wins while running
    if st is None:
        raise HTTPException(404, "generation not found")
    return _gen_public(gid, st)


@router.get("/gen/{gid}/image/{name}")
async def gen_image(gid: str, name: str):
    if any(x in gid + name for x in ("/", "\\", "..")):
        raise HTTPException(400, "bad name")
    fp = _gen_dir(gid) / name
    if not fp.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(fp), media_type="image/png")


@router.get("/health")
async def health(request: Request):
    disp = _dispatcher(request)
    workers = []
    try:
        for w in (getattr(disp, "workers", {}) or {}).values():
            workers.append({
                "url": _short_worker(w.url),
                "healthy": bool(getattr(w, "healthy", False)),
                "klein": "klein" in (getattr(w, "capabilities", set()) or set()),
                "in_flight": getattr(w, "in_flight", None),
            })
    except Exception:  # noqa: BLE001
        pass
    return {"workers": workers,
            "klein_worker_online": any(w["healthy"] and w["klein"] for w in workers)
                                   or (_klein_worker(disp)[1] is not None),
            "pose_count": len(_read_poses()),
            "upscale_workflow": (_WORKFLOWS_DIR / "STUDIO_UPSCALE.json").exists(),
            "klein_workflows_ok": all(
                (_WORKFLOWS_DIR / f"KLEIN_EDIT_ULTRA_WORKFLOW_{n}REF.json").exists()
                for n in (1, 2, 3))}
