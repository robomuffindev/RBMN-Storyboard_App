"""Image Workshop API — a free-form model playground with a persistent gallery.

The Workshop is a place to experiment with our image models outside any single
character's flow: type a freestyle prompt (or fill the same creator-style
character fields we use elsewhere, optionally auto-filled by the LLM wizard),
optionally feed 1–5 reference images (uploaded OR picked from the gallery),
pick a model, generate a batch, review the grid, then SAVE the keepers into one
shared global gallery.  Saved images can be downloaded, deleted, or fed back in
as references for the next experiment.

Reuse, not reinvention: generation rides the SAME worker workflows the rest of
the app uses (`backend.services.comfyui.workflow`), dispatched through the SAME
ComfyUI dispatcher.  Text-to-image runs z_image / krea2 / anima / klein; when
reference images are supplied, klein routes through the KLEIN_EDIT_*REF graphs
and `qie` routes through the Studio Qwen-Image-Edit graph.

Storage layout (under <project_dir>/_libraries/workshop/):
    gallery/<id>.png          saved gallery images
    gallery/index.json        gallery metadata list (newest first)
    refs/<id>.png             uploaded reference images
    _gen/_gen_<gid>/          in-flight generation batches (status.json + N.png)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings as cfg
from backend.services.comfyui.workflow import (
    prepare_zimage_workflow, prepare_krea2_workflow,
    prepare_anima_workflow, prepare_klein_workflow,
    prepare_studio_qie_edit_workflow,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/image-workshop", tags=["image-workshop"])

# ── Paths ────────────────────────────────────────────────────────────────────
_WS_ROOT = Path(cfg.project_dir) / "_libraries" / "workshop"
_WS_GALLERY = _WS_ROOT / "gallery"
_WS_REFS = _WS_ROOT / "refs"
_WS_GEN = _WS_ROOT / "_gen"
_GALLERY_INDEX = _WS_GALLERY / "index.json"
_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows"

_BG_TASKS: set = set()   # keep strong refs to background generation tasks


def _ensure_dirs() -> None:
    for d in (_WS_GALLERY, _WS_REFS, _WS_GEN):
        d.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dispatcher(request: Request):
    return getattr(request.app.state, "comfy_dispatcher", None)


# ── Model catalog ────────────────────────────────────────────────────────────
# refs = max reference images the model consumes (0 = text-to-image only).
# cap  = required worker capability (None = runs on any healthy worker).
WS_MODELS: dict[str, dict] = {
    "z_image": {"label": "Z-Image Turbo", "refs": 0, "cap": None,
                "note": "Fast, photoreal text-to-image."},
    "krea2":   {"label": "Krea 2 Turbo", "refs": 0, "cap": None,
                "note": "Crisp text-to-image."},
    "anima":   {"label": "Anima (anime)", "refs": 0, "cap": None,
                "note": "Anime/illustration; supports a negative prompt."},
    "klein":   {"label": "Klein 9B", "refs": 5, "cap": "klein",
                "note": "Flux.2 Klein — text-to-image, or edit with up to 5 references."},
    "qie":     {"label": "Qwen-Image-Edit", "refs": 2, "cap": "vnccs",
                "note": "Reference edit (needs 1–2 references). Great at composing two images."},
}


def _model_supports_refs(model: str) -> int:
    return int(WS_MODELS.get(model, {}).get("refs", 0) or 0)


def _pick_worker(disp, model: str):
    """Healthy worker for a model. Klein/qie need a capability; the plain t2i
    generators run anywhere. Falls back to any worker if the preferred cap has
    no match, so a mislabelled worker still gets a shot (the render error, if
    any, then surfaces honestly)."""
    if not disp:
        return None, None
    cap = WS_MODELS.get(model, {}).get("cap")
    caps = {cap} if cap else set()
    w = None
    try:
        w = disp.select_worker(caps, set(), exclude_runpod=True)
    except Exception:
        w = None
    if not w and caps:
        try:
            w = disp.select_worker(set(), set(), exclude_runpod=True)
        except Exception:
            w = None
    if not w:
        return None, None
    return w, disp.clients.get(w.url)


# ── Prompt composition (character mode) ──────────────────────────────────────
# Mirrors how the studio turns creator fields into a natural prompt: name +
# ordered descriptive slots joined into one clause.  Kept deliberately simple so
# the Workshop stays a transparent playground, not a hidden pipeline.
_CHAR_FIELD_ORDER = [
    ("age", "{v} year old"),
    ("sex", "{v}"),
    ("race", "{v}"),
    ("skin_color", "{v} skin"),
    ("hair", "{v} hair"),
    ("eyes", "{v} eyes"),
    ("face", "{v}"),
    ("body", "{v} build"),
    ("height", "{v}"),
    ("aesthetics", "{v}"),
    ("additional_details", "{v}"),
]


def _compose_character_prompt(name: str, fields: dict) -> str:
    parts: list[str] = []
    nm = (name or "").strip()
    if nm:
        parts.append(nm)
    for key, tmpl in _CHAR_FIELD_ORDER:
        raw = fields.get(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if not val:
            continue
        parts.append(tmpl.format(v=val))
    body = ", ".join(p for p in parts if p)
    prefix = "full body portrait of " if nm else "full body portrait, "
    return (prefix + body) if body else (nm or "a character")


# ── Reference resolution ─────────────────────────────────────────────────────
def _ref_local_path(ref: dict) -> Optional[Path]:
    """Resolve a {source, id} reference descriptor to a local file path."""
    src = str(ref.get("source") or "").strip()
    rid = str(ref.get("id") or "").strip()
    if not rid or "/" in rid or "\\" in rid or ".." in rid:
        return None
    if src == "gallery":
        p = _WS_GALLERY / f"{rid}.png"
    elif src == "upload":
        p = _WS_REFS / f"{rid}.png"
    else:
        return None
    return p if p.exists() else None


def _save_png(raw: bytes, dest: Path) -> None:
    """Normalise arbitrary uploaded bytes to a PNG on disk (RGBA-safe)."""
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")


# ── Gallery persistence ──────────────────────────────────────────────────────
def _read_gallery() -> list[dict]:
    if not _GALLERY_INDEX.exists():
        return []
    try:
        data = json.loads(_GALLERY_INDEX.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_gallery(items: list[dict]) -> None:
    _WS_GALLERY.mkdir(parents=True, exist_ok=True)
    _GALLERY_INDEX.write_text(json.dumps(items, indent=2), "utf-8")


def _gallery_public(it: dict) -> dict:
    return {
        "id": it["id"],
        "url": f"/api/image-workshop/gallery/{it['id']}/image",
        "prompt": it.get("prompt", ""),
        "model": it.get("model", ""),
        "mode": it.get("mode", "freestyle"),
        "seed": it.get("seed"),
        "width": it.get("width"),
        "height": it.get("height"),
        "fields": it.get("fields"),
        "negative": it.get("negative", ""),
        "tags": it.get("tags", []),
        "created_at": it.get("created_at"),
    }


def _norm_tags(tags) -> list[str]:
    """Clean a tag list: strip, drop blanks, de-dupe (case-insensitive, keeping
    the first spelling), cap length + count so the index stays tidy."""
    out: list[str] = []
    seen: set[str] = set()
    for t in (tags or []):
        s = str(t).strip()[:40]
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= 12:
            break
    return out


# ── Generation batch status ──────────────────────────────────────────────────
def _gen_dir(gid: str) -> Path:
    return _WS_GEN / f"_gen_{gid}"


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


def _run_prompt_blocking(client, workflow: dict, timeout_s: float = 300.0):
    """Queue a raw workflow, poll /history, return (outputs, pid). Sync."""
    import time
    res = client.queue_prompt(workflow)
    pid = res.get("prompt_id") or res.get("promptId") if isinstance(res, dict) else None
    if not pid:
        raise RuntimeError("worker did not return a prompt_id")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(1.2)
        try:
            hist = client.get_full_history(pid)
        except Exception:
            continue
        entry = hist.get(pid) if isinstance(hist, dict) else None
        if entry and entry.get("outputs"):
            return entry["outputs"], pid
    raise TimeoutError("worker timed out")


def _images_from_outputs(outputs: dict) -> list:
    out: list = []
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for f in (node_out.get("images") or []):
            if isinstance(f, dict) and f.get("filename") and f.get("type") != "temp":
                out.append({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                            "type": f.get("type", "output")})
    return out


def _t2i_workflow(model: str, prompt: str, negative: str, w: int, h: int, seed: int) -> dict:
    files = {
        "z_image": "ZIMAGE_TURBO_T2I.json",
        "krea2": "KREA2_TURBO_T2I.json",
        "anima": "ANIMA_T2I.json",
        "klein": "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json",
    }
    fname = files.get(model)
    if not fname:
        raise ValueError(f"model {model!r} has no text-to-image graph")
    path = _WORKFLOWS_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"workflow {fname} not found")
    if model == "z_image":
        return prepare_zimage_workflow(str(path), prompt, w, h, seed)
    if model == "krea2":
        return prepare_krea2_workflow(str(path), prompt, w, h, seed)
    if model == "anima":
        return prepare_anima_workflow(str(path), prompt, w, h, seed, negative)
    return prepare_klein_workflow(str(path), prompt, w, h, seed)


def _klein_edit_workflow(prompt: str, w: int, h: int, seed: int, ref_names: list[str]) -> dict:
    n = max(1, min(len(ref_names), 5))
    fname = f"KLEIN_EDIT_ULTRA_WORKFLOW_{n}REF.json"
    path = _WORKFLOWS_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"workflow {fname} not found")
    return prepare_klein_workflow(str(path), prompt, w, h, seed, ref_images=ref_names[:n])


def _qie_edit_workflow(prompt: str, seed: int, ref_names: list[str], target: int) -> dict:
    path = _WORKFLOWS_DIR / "STUDIO_QIE_EDIT.json"
    if not path.exists():
        raise FileNotFoundError("workflow STUDIO_QIE_EDIT.json not found")
    img1 = ref_names[0]
    img2 = ref_names[1] if len(ref_names) > 1 else ref_names[0]
    return prepare_studio_qie_edit_workflow(
        str(path), image1_path=img1, image2_path=img2, prompt=prompt,
        seed=seed, target_size=target, latent_image_index=1)


async def _run_gen(gid: str, disp, model: str, prompt: str, negative: str, count: int,
                   w: int, h: int, base_seed: int, ref_paths: list[str],
                   meta: Optional[dict] = None) -> None:
    """Background task: generate `count` images, updating status.json per image."""
    try:
        st = {"status": "running", "model": model, "prompt": prompt, "total": count,
              "done": 0, "images": [], "error": None,
              "mode": (meta or {}).get("mode", "freestyle"),
              "fields": (meta or {}).get("fields"), "negative": negative,
              "width": w, "height": h}
        _write_gen(gid, st)
        gd = _gen_dir(gid)
        target = max(w, h)
        errs: list[str] = []
        for i in range(count):
            try:
                _worker, client = _pick_worker(disp, model)
                if not client:
                    raise RuntimeError(
                        f"no worker online for {WS_MODELS.get(model, {}).get('label', model)}"
                        + (f" (needs '{WS_MODELS[model]['cap']}')" if WS_MODELS.get(model, {}).get("cap") else ""))
                # Upload references fresh to THIS worker (uploads are per-worker).
                ref_names: list[str] = []
                for rp in ref_paths:
                    up = f"ws_ref_{uuid4().hex[:8]}.png"
                    await asyncio.to_thread(client.upload_image, rp, up)
                    ref_names.append(up)

                if ref_names and model == "klein":
                    wf = _klein_edit_workflow(prompt, w, h, base_seed + i, ref_names)
                elif ref_names and model == "qie":
                    wf = _qie_edit_workflow(prompt, base_seed + i, ref_names, target)
                elif model == "qie":
                    raise RuntimeError("Qwen-Image-Edit needs at least one reference image")
                else:
                    wf = _t2i_workflow(model, prompt, negative, w, h, base_seed + i)

                outputs, _pid = await asyncio.to_thread(_run_prompt_blocking, client, wf, 360)
                imgs = _images_from_outputs(outputs)
                if not imgs:
                    raise RuntimeError("worker produced no image")
                pick = imgs[-1]
                data = await asyncio.to_thread(client.download_output, pick["filename"],
                                               pick.get("subfolder", ""), pick.get("type", "output"))
                name = f"{i}.png"
                (gd / name).write_bytes(data)
                st["images"].append({"id": name, "name": name, "seed": base_seed + i})
            except Exception as e:
                errs.append(f"#{i + 1}: {type(e).__name__}: {e}")
                logger.warning(f"[workshop-gen {gid}] image {i} failed: {e}")
            st["done"] = i + 1
            st["error"] = "; ".join(errs[-3:]) if errs else None
            _write_gen(gid, st)
        st["status"] = "done" if st["images"] else "error"
        if not st["images"] and not st["error"]:
            st["error"] = "all generations failed"
        _write_gen(gid, st)
    except Exception as e:  # never let the bg task die silently
        logger.error(f"[workshop-gen {gid}] fatal: {e}")
        _write_gen(gid, {"status": "error", "model": model, "prompt": prompt,
                         "total": count, "done": 0, "images": [], "error": f"{type(e).__name__}: {e}"})


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/models")
async def list_models(request: Request):
    """Model catalog + which are online right now (for graying-out in the UI)."""
    disp = _dispatcher(request)

    def online(m: str) -> bool:
        try:
            _w, c = _pick_worker(disp, m)
            return c is not None
        except Exception:
            return False

    return {"models": [
        {"value": k, "label": v["label"], "refs": v["refs"], "note": v["note"],
         "online": online(k)}
        for k, v in WS_MODELS.items()
    ]}


class GenIn(BaseModel):
    mode: str = "freestyle"                 # 'freestyle' | 'character'
    model: str = "z_image"
    prompt: str = ""                        # freestyle text (or extra text in character mode)
    name: str = ""                          # character mode: character name
    fields: dict = {}                       # character mode: creator-style slots
    negative: str = ""
    count: int = 4
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    references: list[dict] = []             # [{source:'gallery'|'upload', id}]


@router.post("/generate")
async def generate(request: Request, body: GenIn):
    _ensure_dirs()
    if body.model not in WS_MODELS:
        raise HTTPException(400, f"model must be one of {', '.join(WS_MODELS)}")

    # Compose the effective prompt.
    if body.mode == "character":
        prompt = _compose_character_prompt(body.name, body.fields or {})
        if body.prompt.strip():
            prompt = f"{prompt}, {body.prompt.strip()}"
    else:
        prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt is required (freestyle) or at least one character field")

    # Resolve references.
    max_refs = _model_supports_refs(body.model)
    ref_paths: list[str] = []
    for ref in (body.references or []):
        p = _ref_local_path(ref)
        if p:
            ref_paths.append(str(p))
    if ref_paths and max_refs == 0:
        raise HTTPException(400, f"{WS_MODELS[body.model]['label']} does not accept reference images")
    ref_paths = ref_paths[:max_refs]
    if body.model == "qie" and not ref_paths:
        raise HTTPException(400, "Qwen-Image-Edit needs at least one reference image")

    disp = _dispatcher(request)
    _worker, client = _pick_worker(disp, body.model)
    if not client:
        raise HTTPException(409, "No suitable worker online" +
                            (f" (needs '{WS_MODELS[body.model]['cap']}')" if WS_MODELS[body.model].get("cap") else ""))

    count = max(1, min(int(body.count or 1), 8))
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)
    gid = uuid4().hex[:12]
    meta = {"mode": body.mode, "fields": (body.fields or None) if body.mode == "character" else None}
    _write_gen(gid, {"status": "running", "model": body.model, "prompt": prompt,
                     "total": count, "done": 0, "images": [], "error": None,
                     "mode": meta["mode"], "fields": meta["fields"],
                     "negative": body.negative.strip(), "width": w, "height": h})
    task = asyncio.create_task(_run_gen(gid, disp, body.model, prompt, body.negative.strip(),
                                        count, w, h, base_seed, ref_paths, meta))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return {"gen_id": gid, "total": count, "model": body.model, "prompt": prompt, "seed": base_seed}


@router.get("/gen/{gid}")
async def gen_status(gid: str):
    st = _read_gen(gid)
    if st is None:
        raise HTTPException(404, "generation not found")
    imgs = [{"id": im["id"], "url": f"/api/image-workshop/gen/{gid}/image/{im['id']}",
             "seed": im.get("seed")} for im in st.get("images", [])]
    return {"gen_id": gid, "status": st.get("status"), "done": st.get("done", 0),
            "total": st.get("total", 0), "model": st.get("model"), "prompt": st.get("prompt", ""),
            "images": imgs, "error": st.get("error")}


@router.get("/gen/{gid}/image/{name}")
async def gen_image(gid: str, name: str):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "bad name")
    fp = _gen_dir(gid) / name
    if not fp.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(fp), media_type="image/png")


# ── References (upload / list / serve / delete) ──────────────────────────────
@router.post("/upload")
async def upload_ref(file: UploadFile = File(...)):
    _ensure_dirs()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    rid = uuid4().hex[:12]
    try:
        _save_png(raw, _WS_REFS / f"{rid}.png")
    except Exception as e:
        raise HTTPException(400, f"unreadable image: {e}")
    return {"id": rid, "source": "upload",
            "url": f"/api/image-workshop/refs/{rid}/image",
            "name": file.filename or f"{rid}.png"}


@router.get("/refs/{rid}/image")
async def ref_image(rid: str):
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    fp = _WS_REFS / f"{rid}.png"
    if not fp.exists():
        raise HTTPException(404, "reference not found")
    return FileResponse(str(fp), media_type="image/png")


# ── Gallery (save / list / serve / delete) ───────────────────────────────────
class SaveIn(BaseModel):
    gen_id: str
    image_ids: list[str] = []
    tags: list[str] = []                     # optional category tags for these saves


@router.post("/save")
async def save_to_gallery(body: SaveIn):
    _ensure_dirs()
    st = _read_gen(body.gen_id)
    if st is None:
        raise HTTPException(404, "generation not found")
    gd = _gen_dir(body.gen_id)
    seed_by_id = {im.get("id"): im.get("seed") for im in st.get("images", [])}
    valid = [i for i in (body.image_ids or [])
             if (gd / i).exists() and "/" not in i and "\\" not in i and ".." not in i]
    if not valid:
        raise HTTPException(400, "no valid image_ids")
    tags = _norm_tags(body.tags)
    items = _read_gallery()
    saved: list[dict] = []
    for img_id in valid:
        new_id = uuid4().hex[:12]
        dest = _WS_GALLERY / f"{new_id}.png"
        dest.write_bytes((gd / img_id).read_bytes())
        rec = {
            "id": new_id, "file": f"{new_id}.png",
            "prompt": st.get("prompt", ""), "model": st.get("model", ""),
            "mode": st.get("mode", "freestyle"), "seed": seed_by_id.get(img_id),
            "width": st.get("width"), "height": st.get("height"),
            "fields": st.get("fields"), "negative": st.get("negative", ""),
            "tags": list(tags), "created_at": _now_iso(),
        }
        items.insert(0, rec)
        saved.append(_gallery_public(rec))
    _write_gallery(items)
    return {"saved": saved, "count": len(saved)}


def _all_tags(items: list[dict]) -> list[str]:
    """Distinct tags across the whole gallery, most-used first (for filter chips)."""
    counts: dict[str, int] = {}
    order: dict[str, str] = {}
    for it in items:
        for t in (it.get("tags") or []):
            k = str(t).lower()
            counts[k] = counts.get(k, 0) + 1
            order.setdefault(k, str(t))
    return [order[k] for k in sorted(counts, key=lambda k: (-counts[k], k))]


@router.get("/gallery")
async def gallery_list(offset: int = 0, limit: int = 200, q: str = "", model: str = "", tag: str = ""):
    items = _read_gallery()
    all_tags = _all_tags(items)
    if q:
        ql = q.lower()
        items = [it for it in items if ql in (it.get("prompt", "") or "").lower()]
    if model:
        items = [it for it in items if it.get("model") == model]
    if tag:
        tl = tag.lower()
        items = [it for it in items if any(str(t).lower() == tl for t in (it.get("tags") or []))]
    total = len(items)
    page = items[max(0, offset): max(0, offset) + max(1, min(limit, 500))]
    return {"total": total, "items": [_gallery_public(it) for it in page], "all_tags": all_tags}


class TagIn(BaseModel):
    ids: list[str] = []                      # one or many gallery ids to update
    tags: list[str] = []                     # the full replacement tag set


@router.post("/gallery/tags")
async def gallery_set_tags(body: TagIn):
    """Replace the tag set on one or more gallery items."""
    ids = set(i for i in (body.ids or []) if i and "/" not in i and "\\" not in i and ".." not in i)
    if not ids:
        raise HTTPException(400, "no ids")
    tags = _norm_tags(body.tags)
    items = _read_gallery()
    updated = 0
    for it in items:
        if it["id"] in ids:
            it["tags"] = list(tags)
            updated += 1
    _write_gallery(items)
    return {"updated": updated, "tags": tags}


@router.get("/gallery/{gid}/image")
async def gallery_image(gid: str):
    if "/" in gid or "\\" in gid or ".." in gid:
        raise HTTPException(400, "bad id")
    fp = _WS_GALLERY / f"{gid}.png"
    if not fp.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(fp), media_type="image/png")


class DeleteIn(BaseModel):
    ids: list[str] = []


@router.post("/gallery/delete")
async def gallery_delete(body: DeleteIn):
    ids = set(i for i in (body.ids or []) if i and "/" not in i and "\\" not in i and ".." not in i)
    if not ids:
        raise HTTPException(400, "no ids")
    items = _read_gallery()
    kept = []
    removed = 0
    for it in items:
        if it["id"] in ids:
            try:
                (_WS_GALLERY / f"{it['id']}.png").unlink(missing_ok=True)
            except Exception:
                pass
            removed += 1
        else:
            kept.append(it)
    _write_gallery(kept)
    return {"deleted": removed}
