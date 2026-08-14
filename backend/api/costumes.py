"""👗 Costume Library — design a costume as an IMAGE, reuse it on any character.

v1.276.27.  Lorenzo asked for two things on top of the outfit form:

  1. "describe an outfit and the llm … should fill the fields with the data to
     create the outfit" — `POST /api/costumes/draft`, a TEXT-model pass (not the
     vision one) that turns a sentence into the 13 named garment slots.

  2. "an option after describing the outfit in the fields to generate an image
     of the outfit … so we can modify the descriptions as well as custom prompt
     what we want the outfit to look like. Once done we can choose one of the
     renders … to use as reference for the costume creation on our character.
     … Also KREA2 should be the default."

His call on three design points, and each one shapes the code:

  · **Renders show the costume on a NEUTRAL MANNEQUIN**, not on a person. This
    lane has been bitten repeatedly by a reference carrying more than it was
    meant to — a face, a body, a facing. A featureless dress form carries the
    garment and nothing else. `_MANNEQUIN` is prepended to every design prompt
    and it is affirmative throughout, because Klein-family graphs run at cfg=1
    where "no face" paints a face.

  · **A SHARED library, not per-character.** A costume designed once dresses a
    whole cast, so it lives in `<project>/_libraries/costumes/` and is adopted
    INTO a character on demand (copied to a `garment` ref, so the character
    remains self-contained and deleting a costume never breaks a rendered set).

  · **Adopting a costume vision-scans it back into the slots**, so the text
    describes what was actually rendered rather than what was asked for. Prompt
    and reference agreeing is the thing that stops drift.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import (APIRouter, Depends, File, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/costumes", tags=["costumes"])

_ROOT = Path(cfg.project_dir) / "_libraries" / "costumes"
_INDEX = _ROOT / "index.json"
_BG: set = set()
_JOBS: Dict[str, dict] = {}
_LOCK = __import__('threading').Lock()   # index.json is read-modify-write


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> List[dict]:
    try:
        d = json.loads(_INDEX.read_text("utf-8"))
        return d if isinstance(d, list) else []
    except Exception:                                # noqa: BLE001
        return []


def _write(items: List[dict]) -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), "utf-8")
    tmp.replace(_INDEX)


def _img(cid: str) -> Path:
    return _ROOT / "img" / f"{cid}.png"


def _url(cid: str) -> str:
    try:
        rev = int(_img(cid).stat().st_mtime)
    except OSError:
        rev = 0
    return f"/api/costumes/{cid}/image?v={rev}"       # versioned, see _ref_url


# ── the prompt ───────────────────────────────────────────────────────────────
#: ⚠ AFFIRMATIVE ONLY. These graphs run at cfg=1 with no negative-prompt node,
#: so "no head", "faceless", "not a person" all summon a person. The mannequin
#: is described as the positive thing it is.
#: ⚠ v1.276.28 — NO STAND. The first design used "a plain dress form … standing
#: on a slim metal stand", and the stand is IN THE IMAGE, so when that image is
#: used as a garment reference Klein faithfully copies the pole into the render
#: of the character. Lorenzo: "the bar thats part of the manequine … is showing
#: from the costume image."
#:
#: Telling the outfit prompt to leave the pole out would be a negation, and at
#: cfg=1 that paints a pole. So the stand is removed AT THE SOURCE by changing
#: what the mannequin IS: a FULL-BODY mannequin with legs and feet, standing on
#: the floor under its own weight, has nowhere to put a pole. It also gives the
#: footwear actual feet to sit on, which a dress form never had.
#: v1.276.30 — WHO is it cut for. Lorenzo: "make it so we have to define if its
#: for a woman, man, or if its unisex." It matters twice: the garment is cut
#: differently, and a swimsuit on the wrong form is simply wrong. The mannequin
#: keeps its blank head and matte-grey finish in every case — only the
#: PROPORTIONS change, so nothing identifying enters the reference.
_WEARERS = {
    "woman": "a female-form mannequin with a woman's proportions",
    "man": "a male-form mannequin with a man's proportions",
    "unisex": "a neutral-form mannequin with androgynous proportions",
}


def _wrap(garments: str, wearer: str = "unisex") -> str:
    """The costume prompt: GARMENTS FIRST, mannequin framing after.

    ⚠ v1.276.30 — I TRIED GARMENTS-FIRST AND IT WAS WORSE. RECORDED SO IT IS
    NOT RETRIED. A bikini request under the original mannequin-first wording
    produced a floor-length dress, so the theory was that ~60 words of staging
    before the garment diluted it. Leading with the garments instead produced
    **bare mannequins wearing nothing at all**, twice, at the same seed — a
    strictly worse failure. Mannequin-first is restored: it is the wording the
    Desert scavenger set was built with and it reliably clothes the figure.

    ⚠ CORRECTED v1.276.34: I originally wrote that "minimal swimwear does not
    render on this mannequin with Krea 2" after four failed renders. TOO STRONG
    — Lorenzo's own run produced a clean green bikini on a mannequin with Krea 2
    at the same settings. Swimwear is LESS RELIABLE than a coat here, not
    impossible; attach a reference image (which is then vision-scanned) when it
    matters.
    """
    form = _WEARERS.get(wearer, _WEARERS["unisex"])
    return (
        f"A costume worn by a smooth featureless matte-grey full-body "
        f"mannequin — {form}, a complete figure with arms, legs and feet and a "
        "smooth blank rounded head, standing upright on both bare feet flat on "
        "a seamless white floor, weight on its own legs, arms relaxed at its "
        "sides, photographed straight on against a plain white studio "
        "background with soft even lighting. The mannequin is dressed in "
        f"{garments}, and these garments are the subject of the photograph and "
        "fill the frame. Sharp product photography, every seam, fastening, trim "
        "and fabric texture clearly visible, true colours, plain empty "
        "background, the mannequin's bare feet directly on the floor.")


def _mannequin(wearer: str = "unisex") -> str:      # kept for the docs/tests
    return _wrap("The garments", wearer)


_MANNEQUIN = _mannequin("unisex")
_MANNEQUIN_TAIL = (
    ". Full-length view from head to floor, the mannequin's own feet resting "
    "directly on the floor. Sharp product photography, every seam, fastening, "
    "trim and fabric texture clearly visible, true colours, plain empty "
    "background."
)

_SLOTS = ("headwear", "eyewear", "outerwear", "top", "underlayer", "belt",
          "bottom", "legwear", "shoes", "gloves", "jewellery", "accessories",
          "carried")

_DRAFT_SYSTEM = (
    "You are a costume designer breaking a described outfit into a wardrobe "
    "list. You invent only what the description implies, and you never add a "
    "garment the description does not call for."
)
_DRAFT_PROMPT = (
    "Turn this outfit description into a wardrobe list.\n\nDESCRIPTION:\n{desc}\n\n"
    "Return STRICT JSON with these keys — OMIT any key the description does not "
    "call for, and never write \"none\" or an empty description:\n  "
    + ", ".join(_SLOTS) + "\n\n"
    "Each value is a short noun phrase naming ONE garment with its COLOUR, "
    "MATERIAL and any distinctive detail — for example \"a cropped red leather "
    "biker jacket with silver zips\". Describe garments only: say nothing about "
    "the person wearing them, their body, their pose or the setting.\n\n"
    "⚠ Do NOT use character names, franchise names or costume titles (no "
    "\"supergirl leotard\", no \"jedi robe\"). Naming a character pulls that "
    "character's whole costume in. Describe the shapes, colours and materials "
    "literally instead."
)


def _parse_slots(text: str) -> Dict[str, str]:
    raw = str(text or "").strip()
    if "{" in raw and "}" in raw:
        raw = raw[raw.index("{"): raw.rindex("}") + 1]
    try:
        data = json.loads(raw)
    except Exception:                                # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    bad = ("none", "n/a", "na", "null", "not specified", "unknown", "nothing")
    out: Dict[str, str] = {}
    for k in _SLOTS:
        v = data.get(k)
        if isinstance(v, dict):                      # some models nest it
            v = ", ".join(str(x) for x in v.values() if x)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        v = str(v or "").strip().strip('"')
        if not v or v.lower() in bad or v.lower().startswith(("no ", "none")):
            continue
        out[k] = v[:200]
    return out


#: The mannequin's own body, misread as clothing. Telling the vision model
#: "the grey form is a mannequin" cut this from 4 phantom garments to 2 — better,
#: not solved, because a matte-grey torso genuinely looks like a grey top. So the
#: remainder is filtered in CODE, and ONLY where the costume never asked for that
#: slot: if you designed an actually-grey shirt it stays, because it is in the
#: request. Prompting alone was not enough; this is the belt to its braces.
_MANNEQUIN_GREY = ("grey", "gray", "matte grey", "matte gray")
_BODY_NOUNS = ("shirt", "top", "tights", "leggings", "trousers", "pants",
               "bodysuit", "long-sleeved shirt", "undershirt", "base layer",
               "sleeves", "torso")


def _looks_like_the_mannequin(text: str) -> bool:
    """A bare grey basic with no material or detail — i.e. the dress form."""
    t = str(text or "").lower()
    if not any(g in t for g in _MANNEQUIN_GREY):
        return False
    if not any(n in t for n in _BODY_NOUNS):
        return False
    # anything that names a real material or trim is a real garment
    rich = ("leather", "denim", "cotton", "wool", "silk", "knit", "canvas",
            "velvet", "satin", "lace", "mesh", "fur", "pattern", "stripe",
            "print", "embroider", "button", "zip", "pocket", "collar", "trim")
    return not any(w in t for w in rich)


def _slots_to_prompt(slots: Dict[str, str], extra: str = "",
                     wearer: str = "unisex") -> str:
    worn = [str(slots.get(k) or "").strip() for k in _SLOTS
            if str(slots.get(k) or "").strip()]
    if extra.strip():
        worn.append(extra.strip())
    body = ", ".join(worn) if worn else "a plain shirt and plain trousers"
    return _wrap(body, wearer)


# ── 1. describe → slots ──────────────────────────────────────────────────────
class DraftIn(BaseModel):
    description: str
    extra: str = ""


@router.post("/draft")
async def costume_draft(body: DraftIn,
                        session: AsyncSession = Depends(get_session)):
    """A sentence in, thirteen named slots out. Uses the TEXT model."""
    desc = (body.description or "").strip()
    if not desc:
        raise HTTPException(400, "describe the outfit first")
    from backend.api.vnccs_native import _ollama_cfg
    urls, text_model, vision = await _ollama_cfg(session)
    model = text_model or vision
    if not urls or not model:
        raise HTTPException(503, "Ollama is not configured (Settings → Ollama).")
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    try:
        out = await asyncio.to_thread(
            _wiz.ollama_chat_sync, urls, model, _DRAFT_SYSTEM,
            _DRAFT_PROMPT.format(desc=desc), None, 0.3, 180.0, True)
    except Exception as e:                           # noqa: BLE001
        logger.warning("costume draft failed: %s", e)
        raise HTTPException(502, f"the model call failed: {e}")
    slots = _parse_slots(out or "")
    if not slots:
        raise HTTPException(502, "the model returned nothing usable — try a more "
                                 "concrete description")
    return {"slots": slots, "model": model,
            "prompt_preview": _slots_to_prompt(slots, body.extra or "")}


# ── 2. design renders ────────────────────────────────────────────────────────
class DesignIn(BaseModel):
    name: str = ""
    slots: Dict[str, str] = {}
    extra: str = ""                    # free text appended to the garments
    #: ⚠ v1.276.30 — this is the GARMENT DESCRIPTION, and it is still wrapped in
    #: the mannequin framing. It used to be a FULL override that replaced the
    #: whole prompt, which threw the mannequin away the moment anyone typed in
    #: the box: Lorenzo's bathing-suit set came back as flat product shots and
    #: one cropped body, and the stored prompt was exactly what he typed —
    #: "a 2 piece high waist string bikini set with no footwear" — with no
    #: mannequin wording anywhere in it. Nobody wants that field to cost them
    #: the mannequin. `raw_prompt` is the real escape hatch.
    prompt: str = ""
    raw_prompt: bool = False           # true = send `prompt` verbatim, no wrapper
    wearer: str = "unisex"             # woman | man | unisex — see _WEARERS
    scan_refs: bool = True             # ⭐ v1.276.34 — vision-scan reference[1]
                                       # and BUILD the garment text from it.
                                       # Lorenzo: "when we have a reference
                                       # image we shouldnt need the prompt if im
                                       # logically thinking about this." Right
                                       # about the GARMENT; the prompt still
                                       # carries the mannequin and the wearer,
                                       # because the reference cannot say
                                       # "put this on a grey dress form".
    refs: List[str] = []               # v1.276.33: uploaded reference ids, used
                                       # ONLY by edit models (klein ≤5, qie ≤2).
                                       # A costume built from a photo of a real
                                       # garment is the whole point: describe it
                                       # in words AND show it.
    model: str = "krea2"               # his call: Krea 2 is the default
    count: int = 4
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None


@router.get("/models")
async def costume_models():
    """Every model, WITH its reference capacity.

    v1.276.33 — `refs` is what lets the UI show the reference uploader only for
    models that can actually use one. Lorenzo: "add an image or images as a
    reference when creating a costume that only shows up if an edit model is
    used like klein or qwen. This way we can create reusable costumes from
    image references as part of our costume library."
    """
    from backend.api.image_workshop import WS_MODELS
    return {"models": [{"key": k, "label": v["label"], "note": v.get("note", ""),
                        "refs": int(v.get("refs", 0) or 0)}
                       for k, v in WS_MODELS.items()],
            "default": "krea2"}


# ── reference images for edit models (klein / qwen-image-edit) ───────────────
def _ref_path(rid: str) -> Path:
    return _ROOT / "refs" / f"{rid}.png"


@router.post("/refs")
async def costume_ref_upload(file: UploadFile = File(...)):
    """Stash a reference image for an edit-model costume design."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    rid = uuid4().hex[:12]
    p = _ref_path(rid)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(raw))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        im.save(p, "PNG")
    except Exception as e:                           # noqa: BLE001
        raise HTTPException(400, f"unreadable image: {e}")
    size = None
    try:
        from PIL import Image as _I
        with _I.open(p) as im2:
            size = list(im2.size)
    except Exception:                                # noqa: BLE001
        pass
    return {"id": rid, "url": f"/api/costumes/refs/{rid}/image",
            "name": file.filename or rid, "size": size}


@router.get("/refs/{rid}/image")
async def costume_ref_image(rid: str, v: int = 0):
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    p = _ref_path(rid)
    if not p.exists():
        raise HTTPException(404, "reference not found")
    return FileResponse(str(p), media_type="image/png")


@router.post("/refs/{rid}/delete")
async def costume_ref_delete(rid: str):
    try:
        _ref_path(rid).unlink(missing_ok=True)
    except Exception:                                # noqa: BLE001
        pass
    return {"deleted": rid}


@router.get("/job")
async def costume_job():
    return _JOBS.get("design") or {"status": "idle"}


@router.post("/design")
async def costume_design(body: DesignIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Render `count` images of the costume on a neutral mannequin."""
    from backend.api.image_workshop import (WS_MODELS, _pick_worker,
                                            _t2i_workflow, _images_from_outputs)
    if body.model not in WS_MODELS:
        raise HTTPException(400, f"model must be one of {', '.join(WS_MODELS)}")
    wearer = body.wearer if body.wearer in _WEARERS else "unisex"
    # references are honoured only by models that actually take them
    cap_refs = int(WS_MODELS.get(body.model, {}).get("refs", 0) or 0)
    ref_paths: List[str] = []
    if cap_refs:
        for rid in (body.refs or [])[:cap_refs]:
            p = _ref_path(str(rid))
            if p.exists():
                ref_paths.append(str(p))
    # ⭐ v1.276.34 — WITH A REFERENCE, READ THE GARMENT OFF THE IMAGE.
    # A typed description and a photograph can disagree, and when they do the
    # words win at cfg=1 — which is how a bathing-suit reference came back as
    # something else entirely. Scanning the reference makes the text a
    # DESCRIPTION OF THE PICTURE rather than a second opinion about it.
    scanned_txt = ""
    scanned_slots: Dict[str, str] = {}
    if ref_paths and body.scan_refs and not (body.prompt or "").strip():
        try:
            from backend.api.vnccs_native import _ollama_cfg
            urls, _t, vision = await _ollama_cfg(session)
            if urls and vision:
                from backend.api import klein3 as k3
                from backend.services.character_studio.vnccs_native import wizards as _wiz
                out = await asyncio.to_thread(
                    _wiz.ollama_chat_sync, urls, vision, k3._GARMENT_SYSTEM,
                    k3._GARMENT_PROMPT,
                    [_wiz.image_bytes_to_b64(Path(ref_paths[0]).read_bytes())],
                    0.2, 180.0, True)
                scanned_slots = k3._parse_garment_json(out or "")
                if scanned_slots:
                    scanned_txt = ", ".join(
                        scanned_slots[k] for k in _SLOTS if scanned_slots.get(k))
        except Exception as e:                       # noqa: BLE001
            logger.warning("costume ref scan failed: %s", e)

    custom = (body.prompt or "").strip()
    if custom and body.raw_prompt:
        prompt = custom                          # verbatim, on your head be it
    elif custom:
        prompt = _wrap(custom, wearer)
    elif scanned_txt:
        # the reference, in words, plus the staging the reference cannot supply
        extra = (body.extra or "").strip()
        prompt = _wrap(scanned_txt + (f", {extra}" if extra else ""), wearer)
    else:
        prompt = _slots_to_prompt(body.slots or {}, body.extra or "", wearer)
    if ref_paths and not body.raw_prompt:
        # ⚠ Klein/QIE address references POSITIONALLY — cite the slot numbers or
        # the images are just averaged in (feedback_klein_reference_syntax).
        cite = " and ".join(f"image {k + 1}" for k in range(len(ref_paths)))
        prompt += (f" The garments are exactly the ones shown in {cite} — the "
                   f"same cut, the same colours, the same fabric and the same "
                   f"fastenings, now worn by the mannequin.")
    st = _JOBS.setdefault("design", {})
    if st.get("status") == "running":
        raise HTTPException(409, "a costume design job is already running")
    from backend.api.klein3 import _dispatcher
    disp = _dispatcher(request)
    if disp is None:
        raise HTTPException(503, "no ComfyUI dispatcher")
    n = max(1, min(int(body.count or 4), 8))
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    seed0 = int(body.seed) if body.seed else random.randint(1, 2_000_000_000)
    name = (body.name or "").strip() or "untitled costume"
    st.clear()
    st.update({"status": "running", "total": n, "done": 0, "made": [],
               "error": None, "prompt": prompt, "model": body.model,
               "name": name, "scanned": scanned_slots or None})

    # v1.276.30 — per-image status so a run can be watched. Lorenzo: "we have
    # the ability to see the status of whats going on and what step of what
    # image its at. Just to keep track incase we want to check on it."
    st["items"] = [{"i": k + 1, "status": "queued", "worker": None,
                    "error": None, "id": None} for k in range(n)]
    st["refs"] = len(ref_paths)

    def _one(idx: int, host: Optional[str] = None, worker=None) -> None:
        """Render image `idx` (0-based) and file it as a CANDIDATE.

        `host` / `worker` are assigned by the caller ROUND-ROBIN. ⚠ v1.276.31 —
        they used to be chosen inside each thread by asking the dispatcher, and
        every thread asked at the same instant, before any load had registered,
        so they all picked the SAME box. Lorenzo: "the images are not fanning
        out to all the available workers. they seem to be going to the same
        one." Assigning up front is the only way to guarantee a spread.
        """
        from backend.api.klein2 import _run_prompt_blocking
        it = st["items"][idx]
        it["status"] = "running"
        try:
            if body.model == "krea2":
                # ⚠ Krea 2 does NOT go through the generic t2i path. It has its
                # own lane in forge.py because the Krea 2 box has no decorator
                # custom nodes AND the unet filename in KREA2_TURBO_T2I.json is
                # not what is actually installed — `_krea2_unet()` DISCOVERS it
                # on the host and caches it. Sending the raw workflow gets a
                # flat 400 from /prompt, which the first run of this did.
                from backend.api.forge import (_krea2_core_graph,
                                               _krea2_host, _krea2_render)
                kh = host or _krea2_host()
                it["worker"] = kh
                g = _krea2_core_graph(kh, prompt, w, h, seed0 + idx, None, 1.0)
                data = _krea2_render(kh, g)
            else:
                client = worker
                if client is None:
                    _wk, client = _pick_worker(disp, body.model)
                    it["worker"] = str(getattr(_wk, "url", "worker")).replace(
                        "http://", "").rstrip("/")
                else:
                    it["worker"] = host or "worker"
                if not client:
                    raise RuntimeError(f"no worker online for {body.model}")
                # v1.276.33 — reference images, for EDIT models only. Uploads
                # are per-worker, so they go up fresh to whichever box this
                # image landed on.
                ref_names: List[str] = []
                for rp in ref_paths[:cap_refs]:
                    up = f"cos_ref_{uuid4().hex[:8]}.png"
                    client.upload_image(rp, up)
                    ref_names.append(up)
                if ref_names and body.model == "klein":
                    from backend.api.image_workshop import _klein_edit_workflow
                    wf = _klein_edit_workflow(prompt, w, h, seed0 + idx, ref_names)
                elif ref_names and body.model == "qie":
                    from backend.api.image_workshop import _qie_edit_workflow
                    wf = _qie_edit_workflow(prompt, seed0 + idx, ref_names,
                                            max(w, h))
                else:
                    wf = _t2i_workflow(body.model, prompt, "", w, h, seed0 + idx)
                outs = _run_prompt_blocking(client, wf, 600)
                imgs = _images_from_outputs(outs)
                if not imgs:
                    raise RuntimeError("worker produced no image")
                pick = imgs[-1]
                data = client.download_output(pick["filename"],
                                              pick.get("subfolder", ""),
                                              pick.get("type", "output"))
            cid = uuid4().hex[:12]
            _img(cid).parent.mkdir(parents=True, exist_ok=True)
            _img(cid).write_bytes(data)
            with _LOCK:
                items = _read()
                items.append({"id": cid, "name": name,
                              "slots": (body.slots or {}) or scanned_slots,
                              "extra": body.extra or "",
                              "prompt": prompt, "model": body.model,
                              "wearer": wearer, "seed": seed0 + idx,
                              "refs": list(body.refs or [])[:cap_refs],
                              "scanned_slots": scanned_slots or None,
                              # ⭐ v1.276.30 — everything lands as a CANDIDATE.
                              # Lorenzo: "we need to make sure the generated
                              # images we do with the prompt are in their own
                              # testing area and we approve which ones we want
                              # to add to the actual costume library … This is
                              # going to get really messy fast." He is right —
                              # 4 renders a click fills a library in an hour.
                              "approved": False,
                              "created_at": _now()})
                _write(items)
            it.update({"status": "done", "id": cid})
        except Exception as e:                       # noqa: BLE001
            logger.warning("costume design %d/%d failed: %s", idx + 1, n, e)
            it.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            done = sum(1 for x in st["items"] if x["status"] in ("done", "error"))
            st["done"] = done
            st["made"] = [{"id": x["id"], "url": _url(x["id"])}
                          for x in st["items"] if x.get("id")]

    def _run():
        import threading
        # ⭐ v1.276.31 — KREA 2 FANS OUT TOO. forge.py renders it on ONE box and
        # this inherited that, so four images queued on a single GPU. Checked
        # rather than assumed: `/models/diffusion_models` on all three workers
        # lists `krea2_turbo_fp8.safetensors`, so the single-box rule was a
        # habit, not a hardware limit. `_krea2_core_graph(host, …)` and
        # `_krea2_render(host, …)` both already take the host.
        from backend.api.klein3 import _klein_workers_all
        hosts: List[tuple] = []
        try:
            for url, client in _klein_workers_all(disp):
                bare = str(url).replace("http://", "").replace("https://", "")
                hosts.append((bare.split(":")[0], client, bare))
        except Exception as e:                       # noqa: BLE001
            logger.warning("costume worker list failed: %s", e)
        if not hosts:
            from backend.api.forge import _krea2_host
            hosts = [(_krea2_host(), None, _krea2_host())]
        st["workers"] = [h[2] for h in hosts]

        threads = []
        for k in range(n):
            host, client, bare = hosts[k % len(hosts)]   # round-robin
            if body.model == "krea2":
                threads.append(threading.Thread(target=_one, args=(k, host, None),
                                                daemon=True))
            else:
                threads.append(threading.Thread(target=_one, args=(k, bare, client),
                                                daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        errs = [x["error"] for x in st["items"] if x.get("error")]
        st["error"] = "; ".join(errs[:3]) if errs else None
        ok = [x for x in st["items"] if x["status"] == "done"]
        st["status"] = "done" if ok else "error"
        if not ok and not st["error"]:
            st["error"] = "every render failed"

    task = asyncio.create_task(asyncio.to_thread(_run))
    _BG.add(task)
    task.add_done_callback(_BG.discard)
    return {"started": True, "count": n, "prompt": prompt, "model": body.model}


# ── 3. library ───────────────────────────────────────────────────────────────
@router.get("")
async def costume_list(stage: str = "all", wearer: str = "", q: str = ""):
    """`stage`: candidates | library | all.

    v1.276.30 — everything a design run produces is a CANDIDATE until it is
    approved. Four renders a click fills a library in an hour, and a costume
    library full of rejects is worse than no library.
    """
    all_items = [it for it in _read() if _img(it["id"]).exists()]
    items = all_items
    if stage == "candidates":
        items = [it for it in items if not it.get("approved")]
    elif stage == "library":
        items = [it for it in items if it.get("approved")]
    # v1.276.35 — filter + search, because a wardrobe gets unusable fast.
    if wearer and wearer in _WEARERS:
        items = [it for it in items if (it.get("wearer") or "unisex") == wearer]
    needle = (q or "").strip().lower()
    if needle:
        def _hay(it: dict) -> str:
            return " ".join([
                str(it.get("name") or ""),
                str(it.get("prompt") or ""),
                " ".join(str(v) for v in (it.get("slots") or {}).values()),
                str(it.get("model") or ""), str(it.get("wearer") or ""),
            ]).lower()
        items = [it for it in items if needle in _hay(it)]
    out = [{**it, "url": _url(it["id"]), "approved": bool(it.get("approved")),
            "wearer": it.get("wearer") or "unisex",
            # ℹ the reference images this design was built from, resolvable
            "ref_images": [{"id": r, "url": f"/api/costumes/refs/{r}/image"}
                           for r in (it.get("refs") or [])
                           if _ref_path(str(r)).exists()]}
           for it in items]
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    scoped = ([x for x in all_items if not x.get("approved")] if stage == "candidates"
              else [x for x in all_items if x.get("approved")] if stage == "library"
              else all_items)
    return {"costumes": out, "slot_keys": list(_SLOTS),
            "wearers": list(_WEARERS),
            "by_wearer": {w: sum(1 for it in scoped
                                 if (it.get("wearer") or "unisex") == w)
                          for w in _WEARERS},
            "counts": {
                "candidates": sum(1 for it in _read()
                                  if _img(it["id"]).exists() and not it.get("approved")),
                "library": sum(1 for it in _read()
                               if _img(it["id"]).exists() and it.get("approved"))}}


class ApproveIn(BaseModel):
    name: str = ""                     # optional rename as you approve it
    approved: bool = True


@router.post("/{cid}/approve")
async def costume_approve(cid: str, body: ApproveIn):
    """Promote a candidate into the library (or send one back)."""
    with _LOCK:
        items = _read()
        hit = next((x for x in items if x["id"] == cid), None)
        if not hit:
            raise HTTPException(404, "costume not found")
        hit["approved"] = bool(body.approved)
        if body.name.strip():
            hit["name"] = body.name.strip()
        hit["approved_at"] = _now() if body.approved else None
        _write(items)
    return {"id": cid, "approved": hit["approved"], "name": hit.get("name")}


@router.post("/candidates/clear")
async def costume_clear_candidates():
    """Throw away every unapproved candidate — the 'tidy up' button."""
    with _LOCK:
        items = _read()
        drop = [x for x in items if not x.get("approved")]
        _write([x for x in items if x.get("approved")])
    for x in drop:
        try:
            _img(x["id"]).unlink(missing_ok=True)
        except Exception:                            # noqa: BLE001
            pass
    return {"deleted": len(drop)}


@router.get("/{cid}/image")
async def costume_image(cid: str, v: int = 0):
    if "/" in cid or "\\" in cid or ".." in cid:
        raise HTTPException(400, "bad id")
    p = _img(cid)
    if not p.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(p), media_type="image/png")


class RenameIn(BaseModel):
    name: str = ""
    wearer: Optional[str] = None       # v1.276.35: fix a mis-set cut too


@router.post("/{cid}/rename")
async def costume_rename(cid: str, body: RenameIn):
    with _LOCK:
        items = _read()
        hit = next((x for x in items if x["id"] == cid), None)
        if not hit:
            raise HTTPException(404, "costume not found")
        if (body.name or "").strip():
            hit["name"] = body.name.strip()
        if body.wearer and body.wearer in _WEARERS:
            hit["wearer"] = body.wearer
        _write(items)
    return {"id": cid, "name": hit.get("name"), "wearer": hit.get("wearer")}


@router.post("/{cid}/delete")
async def costume_delete(cid: str):
    items = _read()
    if not any(x["id"] == cid for x in items):
        raise HTTPException(404, "costume not found")
    _write([x for x in items if x["id"] != cid])
    try:
        _img(cid).unlink(missing_ok=True)
    except Exception:                                # noqa: BLE001
        pass
    return {"deleted": cid}


# ── 4. adopt into a character ────────────────────────────────────────────────
class AdoptIn(BaseModel):
    slug: str                          # the Klein 3.0 character to dress
    rescan: bool = True                # vision-scan the image back into slots


@router.post("/{cid}/adopt")
async def costume_adopt(cid: str, body: AdoptIn,
                        session: AsyncSession = Depends(get_session)):
    """Copy a library costume into a character as a `garment` reference.

    COPIED, not linked: the character stays self-contained, so deleting a
    costume from the library can never orphan an outfit that was rendered from
    it. The library is where designs are browsed; the character is where a
    render's inputs live.
    """
    from backend.api import klein3 as k3
    items = _read()
    it = next((x for x in items if x["id"] == cid), None)
    if not it or not _img(cid).exists():
        raise HTTPException(404, "costume not found")
    if not it.get("approved"):
        raise HTTPException(409, "that is still a candidate — approve it into "
                                 "the library first")
    c = k3._load(body.slug)                          # 404s if unknown
    rid = uuid4().hex[:12]
    dst = k3._cdir(body.slug) / "refs" / f"{rid}.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_img(cid), dst)
    rec = {"id": rid, "tag": "garment",
           "name": f"costume: {it.get('name') or 'untitled'}",
           "source": "upload", "created_at": _now(),
           "costume_id": cid,
           "size": list(k3._image_size(dst) or []) or None}
    c.setdefault("refs", []).append(rec)
    k3._save(body.slug, c)

    slots = dict(it.get("slots") or {})
    scanned = {}
    if body.rescan:
        # His call: the text should describe what was RENDERED, not what was
        # asked for — that agreement is what stops the render drifting from the
        # description later.
        try:
            from backend.api.vnccs_native import _ollama_cfg
            urls, _t, vision = await _ollama_cfg(session)
            if urls and vision:
                from backend.services.character_studio.vnccs_native import wizards as _wiz
                # ⚠ MEASURED on the first adopt: the scan read the MANNEQUIN
                # ITSELF as clothing — "gray long-sleeved shirt", "gray tights",
                # "gray leggings", "gray pants" — four phantom garments that
                # would then be rendered onto the character. The plain scan
                # prompt has no way to know the grey form is a dress form, so
                # the costume path says so explicitly. (A vision model handles
                # "is not clothing" fine; that rule is about DIFFUSION prompts.)
                out = await asyncio.to_thread(
                    _wiz.ollama_chat_sync, urls, vision, k3._GARMENT_SYSTEM,
                    k3._GARMENT_PROMPT + (
                        "\n\nIMPORTANT: the figure is a featureless matte-grey "
                        "tailor's mannequin on a stand. Its smooth grey surface, "
                        "grey torso, grey arms and grey legs are the MANNEQUIN "
                        "ITSELF and are NOT clothing — never list them as a "
                        "shirt, top, tights, leggings or trousers. List only the "
                        "actual garments and accessories placed onto it."),
                    [_wiz.image_bytes_to_b64(dst.read_bytes())],
                    0.2, 180.0, True)
                scanned = k3._parse_garment_json(out or "")
                if scanned:
                    asked = set(it.get("slots") or {})
                    dropped = [k for k, v in scanned.items()
                               if k not in asked and _looks_like_the_mannequin(v)]
                    for k in dropped:
                        scanned.pop(k, None)
                    if dropped:
                        logger.info("costume adopt: dropped %s as mannequin body",
                                    ", ".join(dropped))
                    slots = {**slots, **scanned}
        except Exception as e:                       # noqa: BLE001
            logger.warning("costume adopt rescan failed: %s", e)

    return {"ref": rid, "slug": body.slug, "slots": slots,
            "rescanned": bool(scanned), "costume_id": cid,
            "name": it.get("name") or "untitled costume",
            "url": k3._ref_url(body.slug, rid)}
