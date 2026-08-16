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

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings as cfg
from backend.services.comfyui.workflow import (
    prepare_klein_workflow, prepare_studio_upscale_workflow,
    prepare_studio_seedvr2_workflow,
)
from backend.api.klein2 import (          # shared, already-proven helpers
    _WORKFLOWS_DIR, _klein_worker, _run_prompt_blocking, _images_from_outputs,
    _read_poses, _K2_POSES, _pose_desc, _clean_pose_desc,
)
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/klein3", tags=["klein3"])

_K3_ROOT = Path(cfg.project_dir) / "_libraries" / "klein3" / "chars"
_BG_TASKS: set = set()
_JOBS: Dict[str, dict] = {}      # f"{slug}:{kind}" -> {"status","detail","error"}

# v1.276.17: `garment` = a photo of CLOTHING, not of the character. It is never
# an identity reference (it may not even contain a person) and it is never part
# of the core set — it is source material for an outfit.
REF_TAGS = ["front", "back", "left", "right", "face", "outfit", "garment", "other"]
VIEW_TAGS = ["front", "back", "left", "right"]

# v1.275.4: the bar a generated face close-up must clear against the uploaded
# front reference before it is allowed to anchor a whole view set. This is
# likeness.ARC_MATCH — "solid match" — deliberately, not the borderline band:
# the anchor is copied into every downstream job, so its error is the floor for
# everything the character will ever produce.
_ANCHOR_MIN = 0.45

# v1.276.14 — THE UPSCALER WAS AN ANIME MODEL.
# STUDIO_UPSCALE.json ships with `4x_APISR_GRL_GAN_generator.pth` baked in as
# the node default. APISR is "Anime Production Inspired Real-world Super
# Resolution" — on a photoreal face it posterises skin and draws hard black
# line-art strokes through hair, which is exactly what Lorenzo saw on the face
# crop. The boxes already carry two photoreal models nobody was selecting.
# These characters are photoreal, so the default must be too.
_GAN_MODELS_PHOTO = ("4x-ClearRealityV1.pth", "4x_foolhardy_Remacri.pth")
_GAN_MODEL_DEFAULT = "4x-ClearRealityV1.pth"

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
    """Refs with this tag, EXCLUDING anything the verifier rejected.

    ⚠ v1.276.18: rejected renders are kept (he should be able to see what was
    thrown away and why) and they are filed under `other` — which is a tag the
    identity picker reads. Without this filter a wrong-facing "right" view that
    verification caught would come back as an identity reference on the next
    run, which is precisely the drift loop this whole lane keeps re-learning."""
    return [r for r in c.get("refs", [])
            if r.get("tag") == tag and not r.get("rejected")]


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


def _outfit_ref_for_view(slug: str, c: dict, view: str,
                         outfit: Optional[dict]) -> Optional[Tuple[Path, str]]:
    """The image of a chosen OUTFIT for one view, if it exists.

    v1.276.3 — Lorenzo wants a dataset to be able to train on a specific outfit
    rather than only on the character's default base. An outfit view IS a
    dressed full-body render of this character from that angle, so it is a
    legitimate identity base; it just was not reachable before.

    `outfit` is {"name": ..., "variant": ...}; an absent/empty variant means the
    base look. Returns None when that outfit has no image for this view, so the
    caller falls through to the normal base chain rather than failing the row.
    """
    if not outfit or not str(outfit.get("name") or "").strip():
        return None
    want_name = str(outfit["name"]).strip()
    want_var = str(outfit.get("variant") or "").strip()
    hits = []
    for r in c.get("refs", []):
        if r.get("tag") != "outfit":
            continue
        o = r.get("outfit") or {}
        if str(o.get("name") or "").strip() != want_name:
            continue
        if str(o.get("variant") or "").strip() != want_var:
            continue
        if str(o.get("view") or "") != view:
            continue
        fp = _cdir(slug) / "refs" / f"{r['id']}.png"
        if fp.exists():
            hits.append((r.get("created_at") or "", fp))
    if not hits:
        return None
    hits.sort()
    label = f"{view} outfit '{want_name}'" + (f" / {want_var}" if want_var else "")
    return hits[-1][1], label


def _base_for_view(slug: str, c: dict, view: str,
                   mode: Optional[str] = None,
                   outfit: Optional[dict] = None) -> Tuple[Optional[Path], str]:
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
    # v1.276.3: an explicitly chosen outfit outranks every other candidate —
    # it is the only tier the user named directly. Missing view -> fall through.
    hit = _outfit_ref_for_view(slug, c, view, outfit)
    if hit:
        return hit
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
    newest others.

    v1.275.4 — BACK VIEWS ARE NOT IDENTITY REFERENCES. Measured on clonejoan:
    a fresh character has exactly two face-bearing refs (the upload and the
    generated anchor), so the third slot was filled by tag order — and on that
    character the next tag was `back`, a picture of the back of a head with no
    face in it at all. ArcFace finds nothing in it; Klein got a third reference
    that could only contribute hair and outfit while diluting the two that
    carried the face. Back rows now sort LAST and are used only if there is
    genuinely nothing else, so a two-ref list beats a three-ref list padded
    with a faceless one."""
    # v1.275.9 — ONE REF PER TAG, UPLOADS FIRST. The old version took every
    # front-tagged ref before considering any other tag, and views/generate
    # APPENDS a new front ref every time it runs. Measured on clonejoan after a
    # day of experiments: nine front refs, and the three slots Klein actually
    # got were [upload, generated front, generated front] — zero angle
    # information, and the app feeding its own lower-fidelity output back in as
    # identity evidence. That is a drift loop, and it gets worse every run.
    # Now: one ref per tag so the three slots carry three viewpoints, and
    # within a tag an UPLOAD always beats something we generated.
    def _pick(tag: str) -> Optional[dict]:
        cands = [r for r in _refs_by_tag(c, tag)
                 if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
        if not cands:
            return None
        ups = [r for r in cands if r.get("source") == "upload"]
        if ups:
            return ups[-1]
        crops = [r for r in cands if r.get("source") == "crop"]
        if crops:                       # a crop of an upload is still the upload
            return crops[-1]
        return cands[-1]

    ordered: List[dict] = []
    for tag in ("front", "face", "left", "right", "outfit", "other", "back"):
        r = _pick(tag)                  # `back` last: it carries no face at all
        if r is not None:
            ordered.append(r)
    out: List[str] = []
    for r in ordered:
        out.append(str(_cdir(slug) / "refs" / f"{r['id']}.png"))
        if len(out) >= limit:
            break
    return out


# ── 🧭 Does the render actually SHOW the view we asked for? (v1.276.18) ──────
# Lorenzo, after the v1.276.17 fix: "1 of 4 of the generations came out correct
# for the right view… I want the retry option so when we auto gen characters it
# does this itself and we won't end up with an incorrect base as that will
# poison all the other additional tasks in the autogen chain."
#
# He is right that this is the thing to automate: the base set is upstream of
# datasets, LoRAs, sheets and every outfit, so one wrong-facing base view is not
# one bad image, it is a bad ingredient in everything downstream.
#
# The check is FREE — insightface on the CPU, already installed, no worker and
# no GPU. `kps_yaw` is the reliable signal (nose offset from the eye midpoint,
# in half-eye-spans): it needs no 3D model, so it cannot fail the way `yaw` can.
# NEGATIVE = nose toward the LEFT edge of the picture.
#
# Measured on clonejoan's own set:
#     front upload   yaw  -12.4   kps -0.03
#     left  view     yaw  -73.4   kps -2.97
#     right  (bad)   yaw  -72.6   kps -2.65      <- faces LEFT, tagged right
#     right  (good)  yaw  +82.0   kps +3.97
#     back  view     no face detected            <- that IS the verification
_KPS_PROFILE = 1.2     # |kps_yaw| at or above this = a genuine side view
_KPS_FRONTAL = 1.0     # below this = facing the camera
_YAW_FRONTAL = 40.0    # degrees; a front view must not be turned further


def _facing_verdict(path: str | Path, view: str) -> Tuple[bool, str]:
    """(ok, human reason) for 'is this image really the {view} view?'

    A view we cannot measure is reported as OK. This gates RETRIES — spending
    Lorenzo's renders on an unmeasurable maybe is worse than accepting it, and
    an honest "not measured" in the status is worth more than a coin flip.
    """
    try:
        from backend.services import likeness
    except Exception:                                  # noqa: BLE001
        return True, "not measured (likeness unavailable)"
    if not likeness.available():
        return True, "not measured (no face model installed)"
    pv = likeness.pose(path)
    if view == "back":
        # No face is the POINT of a back view. A face means it turned around.
        if pv is None:
            return True, "no face visible — correct for a back view"
        return False, (f"a face is visible (kps {pv.get('kps_yaw')}) — this is "
                       f"not a back view")
    if pv is None:
        return False, "no face detected — expected one for this view"
    k = pv.get("kps_yaw")
    y = pv.get("yaw")
    if k is None:
        return True, "not measured (no keypoint yaw)"
    k = float(k)
    if view == "front":
        if abs(k) < _KPS_FRONTAL and (y is None or abs(float(y)) < _YAW_FRONTAL):
            return True, f"facing the camera (kps {k:+.2f})"
        return False, f"turned away from the camera (kps {k:+.2f}, yaw {y})"
    want_neg = view == "left"                     # left = nose toward LEFT edge
    if abs(k) < _KPS_PROFILE:
        return False, (f"not a side view — barely turned (kps {k:+.2f}, needs "
                       f"|kps| ≥ {_KPS_PROFILE})")
    if (k < 0) != want_neg:
        return False, (f"facing the WRONG WAY for a {view} view "
                       f"(kps {k:+.2f}, yaw {y})")
    return True, f"correct {view} profile (kps {k:+.2f}, yaw {y})"


#: 🪞 THE MIRROR STRATEGY, and why it is not "just flip the left view".
#: Flipping a finished LEFT view would give an image that faces right, but it
#: would also swap every asymmetry the character has — hair parting, a scar, a
#: breast pocket, which hand wears the ring. So instead the REFERENCES are
#: flipped, the model is asked for the OTHER side (the direction it is good at),
#: and the RESULT is flipped back. Two flips cancel: the character comes out
#: with its real chirality and the facing we asked for.
#: Measured base rate for a plain re-roll on the right view: 1 in 4 (his count).
_MIRROR_NOTE = "flip refs → ask for the other side → flip the result back"
#: Whether to take that route on the FIRST attempt for side views. Set from
#: measurement, not taste — see the CHANGELOG entry for the run that fixed it.
_MIRROR_FIRST = False


#: ⭐ v1.276.22 — below this short side, a reference is not carrying detail.
#: Klein scales every reference to ~1MP before it reaches the model, so a 400px
#: web grab is being scaled UP by the graph out of pixels that were never there:
#: the buttons, the weave and the trim cannot be copied because they are not in
#: the file. 768 is deliberately conservative — it fires on web thumbnails and
#: phone crops, not on anything this app produced (832×1216).
_REF_MIN_SIDE = 768


def _ref_url(slug: str, rid: str, download: bool = False) -> str:
    """A reference URL that CHANGES when the file does.

    ⚠ v1.276.25 — Lorenzo: "when I click on the reference image we now see in
    the outfit UI it shows the original size and not the upscaled size that
    should be used." An upscale replaces the file IN PLACE under the same id, so
    the URL never changed and the browser kept serving the copy it already had.
    The image on disk was correct the whole time; the UI was showing a stale
    one. A file-mtime revision in the query string fixes it for good, and costs
    nothing — `?v=` is ignored by the route.
    """
    base = f"/api/klein3/characters/{slug}/refs/{rid}/image"
    try:
        rev = int((_cdir(slug) / "refs" / f"{rid}.png").stat().st_mtime)
    except OSError:
        rev = 0
    q = f"?download=1&v={rev}" if download else f"?v={rev}"
    return base + q


def _image_size(path: str | Path) -> Optional[Tuple[int, int]]:
    """(w, h) or None. Never raises — a size check must not fail an upload."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:                            # noqa: BLE001
        return None


def _upscale_file(src: str | Path, dst: Path, disp, max_side: int = 2048,
                  engine: str = "gan", st: Optional[dict] = None,
                  label: str = "upscale") -> Optional[Path]:
    """Upscale ONE file to a NEW path. Non-destructive, blocking, no ref record.

    ⚠ v1.276.45 — pass `st` and this render REPORTS ITS WORKER. It used to
    publish nothing at all, so a stalled GAN upscale was invisible: not in any
    task map, not in `workers`, and therefore not interruptible by an Autogen
    cancel either. One image on one box is the right shape here — but a render
    nobody can see is a render nobody can debug.

    ⭐ v1.276.37 — needed because the outfit face crop wants a bigger SOURCE,
    not just a bigger result. `_start_ref_upscale` works in place on a stored
    reference, which is wrong here: upscaling the outfit's front render in place
    would change an image he has already approved.

    Returns None on any failure — the caller then falls back to the original,
    because a smaller source beats no source.
    """
    src = Path(src)
    try:
        wf_path = _WORKFLOWS_DIR / "STUDIO_UPSCALE.json"
        if not wf_path.exists():
            return None
        _wk, client = _klein_worker(disp)
        if not client:
            return None
        if st is not None:
            # visible: which box, and that a render is happening at all
            st.setdefault("aux_renders", []).append(
                {"what": label, "worker": str(_wk), "engine": engine})
            for w in ([str(_wk)] if _wk else []):
                if w not in st.setdefault("workers", []):
                    st["workers"].append(w)
        up = f"k3_up1_{uuid4().hex[:8]}.png"
        client.upload_image(str(src), up)
        wf = prepare_studio_upscale_workflow(str(wf_path), image_path=up,
                                             model_name=_GAN_MODEL_DEFAULT)
        outputs = _run_prompt_blocking(client, wf, 300)
        imgs = _images_from_outputs(outputs)
        if not imgs:
            return None
        pick = imgs[-1]
        data = client.download_output(pick["filename"], pick.get("subfolder", ""),
                                      pick.get("type", "output"))
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        cap = max(512, min(int(max_side), 8192))
        if max(im.size) > cap:
            sc = cap / max(im.size)
            im = im.resize((max(1, round(im.width * sc)),
                            max(1, round(im.height * sc))), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "PNG")
        return dst
    except Exception as e:                            # noqa: BLE001
        logger.warning("klein3 _upscale_file(%s) failed: %s", src.name, e)
        return None


def _headshot_of(slug: str, src: str | Path) -> Optional[str]:
    """A head-and-shoulders crop of a full-body render, cached beside it.

    v1.276.24. Used when a full-body outfit render has to act as a reference
    for a CLOSE-UP: Klein copies a reference's framing as readily as its
    content, so handing it the whole body produced a bust shot. Reuses the same
    `_face_crop_box` geometry as the character face anchor, so the crop frames
    the head the way every other close-up in this lane does.

    Returns None if no face is found — the caller then falls back to the full
    image rather than losing the garment evidence entirely."""
    src = Path(src)
    dst = _cdir(slug) / "_mirror" / f"head_{src.stem}.png"   # stem differs for
    #                                        the upscaled copy (big_…), so the
    #                                        two crops never share a cache entry
    try:
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return str(dst)
    except OSError:
        pass
    try:
        from backend.services import likeness
        if not likeness.available():
            return None
        pv = likeness.pose(src)
        # same guard `_face_crop_ref` uses — _face_crop_box needs these keys
        if not pv or pv.get("face_cx") is None or not pv.get("img_w"):
            return None
        box = _face_crop_box(pv)
        from PIL import Image
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.crop(box).save(dst, "PNG")
        return str(dst)
    except Exception as e:                        # noqa: BLE001
        logger.warning("klein3 headshot crop failed for %s: %s", src, e)
        return None


def _flip_png(src: str | Path, dst: Path) -> Path:
    """Mirror an image left-to-right. Used by the 🪞 strategy below."""
    from PIL import Image
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.transpose(Image.FLIP_LEFT_RIGHT).save(dst, "PNG")
    return dst


def _view_ref_paths(slug: str, c: dict, view: str, base: List[str],
                    limit: int = 3) -> Tuple[List[str], int]:
    """Filter a base-set view job's reference list FOR THAT VIEW.

    v1.276.17. Every view used to receive the identical list. On a character
    that already had a left view, that list was [face, front, LEFT] — so the
    RIGHT job was shown a left profile and produced a left-facing pose, which
    is exactly what Lorenzo reported. Klein has no way to know a reference is
    meant as "identity only, ignore the facing"; a profile in the list is a
    profile in the answer.

    The rule is narrow ON PURPOSE: drop the OPPOSITE profile, nothing else.
    A side reference genuinely helps a FRONT render (measured v1.275.9:
    slot 3 = left view took front views 0.3637 -> 0.4498), so this must not
    turn into "strip all the side refs". Shortening the list is fine — a
    two-ref list beats a three-ref list padded with a contradiction.

    ⭐ v1.276.19 — AND THEN A DIRECTION REFERENCE. Dropping the opposite profile
    stopped the render being dragged the wrong way, but it left a side job with
    [face crop, front upload] — **two frontal images**. Nothing in that list
    says which way to turn, so the direction came from the prompt alone against
    a model prior, and the prior won most of the time. It also explains why the
    🪞 mirror retry helped so little: mirroring a frontal reference is very
    nearly a no-op.

    So the opposite profile is no longer dropped — it is **MIRRORED and put
    back**. A mirrored LEFT profile is a RIGHT-facing body, which is exactly the
    thing that was missing. It rides as a pose/angle reference only: identity
    still comes from the face crop and the front upload, so mirroring costs
    nothing (the character's real chirality is carried by images 1 and 2, and
    this repo's premise has always been "the person from image 1 in the pose
    from image 2"). Returns (paths, angle_slot) where angle_slot is the 1-based
    position of the direction reference, or 0 — the prompt must cite it by
    NUMBER, because Klein addresses references positionally.
    """
    if view not in ("left", "right"):
        return list(base)[:limit], 0
    opp = _OPPOSITE_VIEW[view]
    tag_of = {str(_cdir(slug) / "refs" / f"{r['id']}.png"): r.get("tag")
              for r in c.get("refs", [])}
    kept = [p for p in base if tag_of.get(p) != opp]
    d = _direction_ref(slug, c, view)
    if d is None:
        return kept[:limit], 0
    # keep at least the face + front in front of it, then the direction ref
    room = max(2, limit - 1)
    out = kept[:room] + [d]
    return out, len(out)


def _direction_ref(slug: str, c: dict, view: str) -> Optional[str]:
    """A reference that SHOWS which way `view` faces, or None.

    Preference order, and the reasoning matters:
      1. the OPPOSITE profile, MIRRORED — a genuinely different render, so it
         cannot feed this view its own output back;
      2. a VERIFIED same-side view as-is — only if it has actually been
         measured, because an unverified same-side ref is exactly the
         wrong-facing image we are trying to replace.
    """
    opp = _OPPOSITE_VIEW[view]
    cands = [r for r in _refs_by_tag(c, opp)
             if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
    # prefer one that has been measured and passed
    ok = [r for r in cands if r.get("verified")]
    r = (ok or cands)[-1] if cands else None
    if r is not None:
        src = _cdir(slug) / "refs" / f"{r['id']}.png"
        dst = _cdir(slug) / "_mirror" / f"dir_{r['id']}.png"
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                _flip_png(src, dst)
            return str(dst)
        except Exception as e:      # noqa: BLE001
            logger.warning("klein3 direction ref mirror failed: %s", e)
            return None
    same = [r for r in _refs_by_tag(c, view)
            if r.get("verified") and (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
    if same:
        return str(_cdir(slug) / "refs" / f"{same[-1]['id']}.png")
    return None


def _front_ref_path(slug: str, c: dict) -> Optional[Path]:
    """The uploaded front reference — the character's source of truth. Prefers
    an upload over anything this app generated, because a generated front is
    itself a claim under test."""
    fronts = _refs_by_tag(c, "front")
    if not fronts:
        return None
    r = next((x for x in fronts if x.get("source") == "upload"), fronts[-1])
    p = _cdir(slug) / "refs" / f"{r['id']}.png"
    return p if p.exists() else None


def _face_crop_box(pv: Dict[str, Any], aspect: float = 832 / 1024,
                   face_share: float = 0.60, face_at: float = 0.42
                   ) -> Tuple[int, int, int, int]:
    """Head-and-shoulders crop box around a detected face, in pixels.

    `face_share` is how much of the crop's HEIGHT the face box should occupy —
    0.60 is chosen to land near the 0.638 the generated anchors actually
    measured, so the crop and the thing it replaces frame the head the same way
    and Klein sees a like-for-like reference. `face_at` puts the face centre at
    42% of the crop height, which leaves headroom above and collarbone below
    instead of a face floating dead centre.
    """
    W, H = int(pv["img_w"]), int(pv["img_h"])
    fh = float(pv["face_h_ratio"]) * H
    cx = float(pv["face_cx"]) * W
    cy = float(pv["face_cy"]) * H
    ch = fh / max(face_share, 0.05)
    cw = ch * aspect
    x1, y1 = cx - cw / 2.0, cy - ch * face_at
    # Clamp INSIDE the image without changing the shape of the box: slide it
    # back in first, and only shrink if it genuinely cannot fit.
    if cw > W:
        ch *= W / cw
        cw = W
    if ch > H:
        cw *= H / ch
        ch = H
    x1 = max(0.0, min(x1, W - cw))
    y1 = max(0.0, min(y1, H - ch))
    return int(round(x1)), int(round(y1)), int(round(x1 + cw)), int(round(y1 + ch))


def _anchor_score(slug: str, c: dict, face_png: Path) -> Optional[float]:
    """ArcFace cosine of a face close-up against the front reference.

    Returns None when the measurement is unavailable (no insightface, no face
    found) — DEGRADED is not FAILED, and a None must never be read as a bad
    score. CPU-only and cached inside likeness.py, so this costs nothing."""
    try:
        from backend.services import likeness
        front = _front_ref_path(slug, c)
        if front is None:
            return None
        a, b = likeness.embed(face_png), likeness.embed(front)
        if a is None or b is None:
            return None
        return likeness.cosine(a, b)
    except Exception as e:  # noqa: BLE001 — measurement must never break a render
        logger.warning("klein3: anchor scoring unavailable: %s", e)
        return None


def _face_crop_ref(slug: str, c: dict, disp: Any,
                   gan: bool = True, model_name: Optional[str] = None
                   ) -> Optional[Tuple[str, Optional[float], dict]]:
    """Build the face anchor by CROPPING the uploaded front reference.

    v1.275.7. Generating a close-up cost identity every time it was measured —
    three anchors scored 0.4660 / 0.3926 / 0.3499 against Lorenzo's upload,
    and the views built on them landed 0.33-0.39 while matching the anchor at
    0.76-0.82. The pipeline was faithfully reproducing a face that was never
    his. A crop cannot drift: it IS the upload.

    The cost is resolution, and it is real — measured on clonejoan the face box
    in the 1024x1536 upload is **115x150 px**, against 512x654 in a generated
    anchor. So the crop is upscaled with the same proven STUDIO_UPSCALE GAN
    graph the base upscaler uses, in the right order: crop small -> GAN -> fit
    to 832x1024. Blowing up with LANCZOS first and GAN-ing the mush afterwards
    would waste the one advantage the crop has.

    Returns (path, score_vs_front, meta) or None. Never raises: if anything
    here fails the caller falls back to generating a close-up, which is the old
    behaviour and still works.
    """
    from io import BytesIO
    from PIL import Image
    try:
        from backend.services import likeness
        front = _front_ref_path(slug, c)
        if front is None:
            return None
        pv = likeness.pose(front)
        if not pv or pv.get("face_cx") is None or not pv.get("img_w"):
            logger.info("klein3 %s: no face box in the front ref — cannot crop", slug)
            return None

        box = _face_crop_box(pv)
        img = Image.open(front)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        crop = img.crop(box)
        meta: dict = {"crop_box": list(box),
                      "crop_px": [crop.width, crop.height],
                      "face_px": [round(pv["face_w_ratio"] * pv["img_w"]),
                                  round(pv["face_h_ratio"] * pv["img_h"])],
                      "gan": False}

        out_img = crop
        if gan:
            wf_path = _WORKFLOWS_DIR / "STUDIO_UPSCALE.json"
            if wf_path.exists():
                tmp = _cdir(slug) / "refs" / f"_crop_{uuid4().hex[:8]}.png"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                crop.save(tmp, "PNG")
                try:
                    _wk, client = _klein_worker(disp)
                    if client:
                        up = f"k3_facecrop_{uuid4().hex[:8]}.png"
                        client.upload_image(str(tmp), up)
                        wf = prepare_studio_upscale_workflow(
                            str(wf_path), image_path=up,
                            model_name=model_name or _GAN_MODEL_DEFAULT)
                        outs = _run_prompt_blocking(client, wf, 300)
                        imgs = _images_from_outputs(outs)
                        if imgs:
                            pick = imgs[-1]
                            data = client.download_output(
                                pick["filename"], pick.get("subfolder", ""),
                                pick.get("type", "output"))
                            out_img = Image.open(BytesIO(data)).convert("RGB")
                            meta["gan"] = True
                            meta["gan_px"] = [out_img.width, out_img.height]
                            meta["worker"] = _short_worker(getattr(_wk, "url", "?"))
                except Exception as e:  # noqa: BLE001 — GAN is a bonus, not a gate
                    logger.warning("klein3 %s: face-crop GAN upscale failed, "
                                   "using the plain crop: %s", slug, e)
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass

        out_img = out_img.convert("RGB").resize((832, 1024), Image.LANCZOS)
        rid = uuid4().hex[:12]
        p = _cdir(slug) / "refs" / f"{rid}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        out_img.save(p, "PNG")
        c2 = _load(slug)
        c2.setdefault("refs", []).append(
            {"id": rid, "tag": "face",
             "name": ("face crop from upload (GAN-upscaled)" if meta["gan"]
                      else "face crop from upload"),
             "source": "crop", "created_at": _now(), "crop_meta": meta})
        _save(slug, c2)
        return str(p), _anchor_score(slug, _load(slug), p), meta
    except Exception as e:  # noqa: BLE001
        logger.warning("klein3 %s: face crop failed, falling back to a generated "
                       "close-up: %s", slug, e)
        return None


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
        # v1.276.9: this list WHITELISTS fields, so anything added to a ref
        # record is invisible to the UI until it is named here. `upscaled` was
        # written to char.json correctly and still never reached the panel.
        out["refs"] = [{"id": r["id"], "tag": r.get("tag", "other"),
                        "name": r.get("name", ""), "source": r.get("source", "upload"),
                        "created_at": r.get("created_at"),
                        "upscaled": bool(r.get("upscaled")),
                        "upscaled_at": r.get("upscaled_at"),
                        "outfit": r.get("outfit"),
                        # v1.276.18 verification — and yes, this whitelist is
                        # the thing that swallowed `upscaled` in v1.276.9.
                        "verified": r.get("verified"),
                        "verify_note": r.get("verify_note"),
                        "attempts": r.get("attempts"),
                        "mirrored": bool(r.get("mirrored")),
                        "rejected": bool(r.get("rejected")),
                        "superseded": bool(r.get("superseded")),
                        "angle_ref": bool(r.get("angle_ref")),
                        "wanted_view": r.get("wanted_view"),
                        # v1.276.25: real pixel dimensions, so "is this big
                        # enough to be a reference?" is answerable in the UI
                        # instead of guessable.
                        "size": r.get("size") or _image_size(
                            _cdir(slug) / "refs" / f"{r['id']}.png"),
                        "orig_size": r.get("orig_size"),
                        "upscaled_engine": r.get("upscaled_engine"),
                        "small": bool((r.get("size") or [9999, 9999])
                                      and min(r.get("size") or [9999, 9999]) < _REF_MIN_SIDE),
                        "url": _ref_url(slug, r["id"])}
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
    """Single Klein edit; records WHICH worker ran it into ``st['worker']``.

    ⚠⚠ v1.276.45 — **UNUSED, and it contains a trap: `workers[0]` always picks
    the SAME box.** Kept because it is a correct single-edit helper, but if you
    ever call it for more than one image, use `_parallel_klein_edits` instead —
    it is the only thing in this file that actually spreads work. `workers[0]`
    is not a load-balanced choice; it is the head of a list sorted by health
    check, i.e. a hidden pin. Left deliberately so nobody re-derives it.
    """
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
    # v1.276.18: `on_result` may RETURN a follow-up job (a retry) which goes back
    # on the queue. That means a worker thread can no longer exit the moment the
    # queue looks empty — another thread may still be about to produce work — so
    # threads drain against an outstanding counter instead.
    outstanding = [len(jobs)]

    def _loop(url, client):
        sw = _short_worker(url)
        while True:
            with lock:
                if outstanding[0] <= 0:
                    return
            try:
                jb = qq.get(timeout=0.4)
            except _q.Empty:
                continue
            t = tasks.setdefault(jb["key"],
                                 {"worker": None, "status": "queued", "error": None})
            t.update({"worker": sw, "status": "running"})
            try:
                data = _run_klein_edit_on(client, jb["prompt"], jb["refs"],
                                          jb["w"], jb["h"], jb["seed"])
                with lock:
                    follow = on_result(jb, data)
                    if follow:
                        qq.put(follow)
                        outstanding[0] += 1
                t["status"] = "done"
            except Exception as e:  # noqa: BLE001
                t.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
                logger.warning("klein3 parallel job %r on %s failed: %s", jb["key"], sw, e)
            finally:
                with lock:
                    outstanding[0] -= 1

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
async def ref_upload(slug: str, request: Request,
                     file: UploadFile = File(...), tag: str = Form("other"),
                     upscale: bool = Form(True),
                     min_side: int = Form(_REF_MIN_SIDE)):
    raw = await file.read()          # await BEFORE _load: the load→append→save
    if not raw:                      # section then runs without yields, so
        raise HTTPException(400, "empty file")   # concurrent uploads can't clobber
    c = _load(slug)
    rid = uuid4().hex[:12]
    try:
        _save_png_bytes(raw, _cdir(slug) / "refs" / f"{rid}.png")
    except Exception as e:
        raise HTTPException(400, f"unreadable image: {e}")
    size = _image_size(_cdir(slug) / "refs" / f"{rid}.png")
    rec = {"id": rid, "tag": tag if tag in REF_TAGS else "other",
           "name": file.filename or f"{rid}.png", "source": "upload",
           "created_at": _now(),
           "size": list(size) if size else None}
    c.setdefault("refs", []).append(rec)
    _save(slug, c)

    # ⭐ v1.276.25 — auto-upscale applies to EVERY reference upload, not only
    # the garment scan. v1.276.22 only wired it into `outfits/scan`, which
    # missed the ordinary "⬆ Upload reference" path — and that is where a small
    # web grab is most likely to enter the character in the first place.
    up_note = None
    if size and upscale and min(size) < max(64, int(min_side or _REF_MIN_SIDE)):
        try:
            _start_ref_upscale(slug, rid, _dispatcher(request),
                               engine="auto", max_side=2048)
            up_note = (f"{size[0]}×{size[1]} is under {min_side}px — upscaling in "
                       f"the background so it works as a reference")
        except Exception as e:                     # noqa: BLE001
            logger.warning("klein3 ref auto-upscale failed: %s", e)
            up_note = f"{size[0]}×{size[1]} is small, and the upscale could not start"
    return {**rec, "url": _ref_url(slug, rid), "upscaling": up_note}


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
    for suffix in (".png", ".orig.png"):     # v1.276.13: take the sidecar too
        try:
            (_cdir(slug) / "refs" / f"{rid}{suffix}").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    _save(slug, c)
    return {"deleted": rid}


@router.get("/characters/{slug}/refs/{rid}/image")
async def ref_image(slug: str, rid: str, download: int = 0):
    """A single reference image.

    v1.276.1: `?download=1` returns it as an attachment under a MEANINGFUL
    filename — `clonejoan_red-leather_front.png` rather than `9c848c8e8c41.png`.
    Every outfit view is its own standalone image (the grouping into an "outfit"
    is metadata, not a merged file) precisely so it can be handed to something
    else as a reference; a file named after a hex id is useless the moment it
    leaves this app.
    """
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    p = _cdir(slug) / "refs" / f"{rid}.png"
    if not p.exists():
        raise HTTPException(404, "image not found")
    if not download:
        return FileResponse(str(p), media_type="image/png")
    name = f"{slug}_{rid}.png"
    try:
        r = _ref_by_id(_load(slug), rid) or {}
        o = r.get("outfit") or {}
        parts = [slug]
        if o.get("name"):
            def _sl(x: str) -> str:
                return re.sub(r"[^a-z0-9]+", "-", str(x).lower()).strip("-")
            parts.append(_sl(o["name"]))
            if o.get("variant"):
                parts.append(_sl(o["variant"]))
            parts.append(str(o.get("view") or "view"))
        else:
            parts.append(str(r.get("tag") or "ref"))
        name = "_".join(x for x in parts if x) + ".png"
    except Exception:  # noqa: BLE001 — a nice name is a bonus, never a blocker
        pass
    return FileResponse(str(p), media_type="image/png", filename=name)


# ── Missing-view synthesis (Klein N-ref edit, per-view prompt) ───────────────
_VIEW_PROMPTS = {
    "front": ("seen directly from the FRONT, facing the camera"),
    "back": ("seen directly from BEHIND — a full back view showing the back of "
             "the head, hairstyle and outfit from behind"),
    # v1.276.17 — "LEFT-SIDE profile" is a CLASS of shot; the body parts are the
    # nameable things, so the side facing the camera is spelled out. Without it
    # the two side prompts differ by one word and the renders came back
    # identical (Lorenzo: "it keeps giving me the character in a left facing
    # pose"). A left-side view shows her LEFT shoulder and arm nearest the
    # camera; a right-side view shows her RIGHT ones.
    "left": ("seen in a full LEFT-SIDE profile view, her LEFT shoulder and LEFT "
             "arm nearest the camera, her nose and the toes of both shoes "
             "pointing to the viewer's left"),
    "right": ("seen in a full RIGHT-SIDE profile view, her RIGHT shoulder and "
              "RIGHT arm nearest the camera, her nose and the toes of both "
              "shoes pointing to the viewer's right"),
}


def _character_garments(fields: Dict[str, Any]) -> str:
    """The character's clothing, NAMED, for a prompt.

    v1.276.14. `additional_details` is where the 🪄 Analyze step already writes
    the outfit — "olive green t-shirt, high-waisted blue jeans, brown belt,
    brown boots, cross necklace" — and `_view_prompt` was reading only `hair`
    and `body`, throwing that away. Any explicit clothing field wins if one
    exists; otherwise fall back to additional_details.
    """
    for k in ("clothing", "outfit", "wardrobe", "additional_details"):
        v = str(fields.get(k, "") or "").strip().rstrip(".")
        if v:
            return v
    return ""


def _view_prompt(view: str, fields: Dict[str, Any], angle_slot: int = 0) -> str:
    """One view of the base set.

    ⚠ THE RULE THIS GOT WRONG (measured 2026-08-04, and again by Lorenzo on his
    own side views 2026-08-09): **Klein ignores CATEGORY words.** The prompt said
    "SAME outfit as the references", which is exactly a category word — so the
    sides came back in black trousers and black boots with no belt and no
    necklace, while the front was blue jeans, a brown belt and brown boots. The
    garments have to be NAMED, every time, in every job.
    """
    extra = ", ".join(str(fields.get(k, "")).strip() for k in ("hair", "body")
                      if str(fields.get(k, "")).strip())
    worn = _character_garments(fields)
    # Named garments come FIRST and are repeated as an explicit instruction —
    # this is the whole outfit, not a hint.
    outfit_clause = (f"wearing exactly the same clothing as the reference images: "
                     f"{worn}, identical garments in identical colours"
                     if worn else
                     "wearing exactly the same clothing as the reference images, "
                     "identical garments in identical colours")
    # ⭐ v1.276.19: when a DIRECTION reference is in the list, point at it by
    # slot number. Klein addresses references positionally, and a body
    # orientation is far easier to copy from a picture than to derive from
    # words — which is this mode's whole premise.
    angle_clause = ""
    if angle_slot:
        angle_clause = (f" Her head, shoulders, hips and both feet are turned "
                        f"exactly as in image {angle_slot} — copy that body "
                        f"orientation and camera angle from image {angle_slot} "
                        f"exactly, while keeping the face and body of the other "
                        f"reference images.")
    return (f"The exact same person shown in the reference image(s), full body "
            f"shot {_VIEW_PROMPTS[view]}, standing straight with arms relaxed at "
            f"the sides, SAME face, SAME hairstyle and SAME body proportions as "
            f"the references, {outfit_clause}"
            f"{', ' + extra if extra else ''}, "
            f"plain white studio background, even lighting, photorealistic."
            f"{angle_clause}")


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
    face_from_crop: bool = True        # v1.275.7: CROP the anchor out of the
                                       # uploaded front ref instead of generating
                                       # one. A crop cannot drift — it IS the
                                       # upload. False = the old generate path.
    face_crop_gan: bool = True         # GAN-upscale the crop (STUDIO_UPSCALE)
    ref_count: int = 3                 # v1.275.10: how many identity refs a view
                                       # job gets (1-5; those workflows all
                                       # exist). Was hardcoded 3 and 3 MEASURED
                                       # BEST: adding a 4th ref (the right
                                       # profile) dropped a frontal render from
                                       # 0.4498 to 0.3797. Knob exposed because
                                       # the right answer may differ per
                                       # character, but do not raise it blind.
    verify: bool = True                # v1.276.18: measure each finished view
                                       # and re-render it if it shows the wrong
                                       # thing. FREE (CPU insightface) — the
                                       # check never costs a render, only the
                                       # retry does.
    max_tries: int = 3                 # attempts PER VIEW including the first
    mirror_first: Optional[bool] = None  # 🪞 use the mirror route on attempt 1
                                       # for side views instead of paying for a
                                       # failed attempt first. None = the
                                       # measured default (_MIRROR_FIRST).
    mirror_retry: bool = True          # 🪞 side views: retry by mirroring the
                                       # references and asking for the OTHER
                                       # side, then flipping the result back —
                                       # a double flip restores the character's
                                       # real chirality. See _MIRROR_NOTE.


class ViewsVerifyIn(BaseModel):
    """Check EXISTING view references without rendering anything (v1.276.18).

    Free: CPU insightface, no worker, no GPU. `demote` files anything that
    fails under `other` + rejected so it stops being used as a reference —
    which also makes the view show up as MISSING, so ＋ missing will refill it.
    """
    demote: bool = False


@router.post("/characters/{slug}/views/verify")
async def views_verify(slug: str, body: ViewsVerifyIn):
    c = _load(slug)
    rows, demoted = [], 0
    for r in list(c.get("refs", [])):
        tag = r.get("tag")
        if tag not in VIEW_TAGS or r.get("rejected"):
            continue
        p = _cdir(slug) / "refs" / f"{r['id']}.png"
        if not p.exists():
            continue
        ok, why = _facing_verdict(p, tag)
        rows.append({"id": r["id"], "view": tag, "ok": ok, "why": why,
                     "source": r.get("source"),
                     "url": f"/api/klein3/characters/{slug}/refs/{r['id']}/image"})
        r["verified"], r["verify_note"] = ok, why
        if not ok and body.demote:
            r["tag"] = "other"
            r["rejected"] = True
            r["wanted_view"] = tag
            r["name"] = f"REJECTED {tag} view — {why}"
            demoted += 1
    _save(slug, c)
    bad = [x for x in rows if not x["ok"]]
    return {"checked": len(rows), "failed": len(bad), "demoted": demoted,
            "rows": rows,
            "missing_views": [v for v in VIEW_TAGS if not _refs_by_tag(c, v)]}


@router.post("/characters/{slug}/views/generate")
async def views_generate(slug: str, body: ViewsIn, request: Request):
    c = _load(slug)
    todo = [v for v in (body.views or []) if v in VIEW_TAGS]
    if not todo:
        raise HTTPException(400, f"views must be from {', '.join(VIEW_TAGS)}")
    nref = max(1, min(int(body.ref_count or 3), 5))   # 1..5 workflows exist;
    #                                                   3 MEASURED BEST (v1.275.10)
    id_refs = _identity_ref_paths(slug, c, limit=nref)
    if not id_refs:
        raise HTTPException(409, "upload at least one reference first")
    st = _job(slug, "views")
    if st.get("status") == "running":
        raise HTTPException(409, "a view-generation job is already running")
    disp = _dispatcher(request)
    seed0 = int(body.seed) if body.seed else random.randint(1, 2_000_000_000)
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    verify = bool(body.verify)
    max_tries = max(1, min(int(body.max_tries or 3), 6))
    mirror_retry = bool(body.mirror_retry)
    mirror_first = (_MIRROR_FIRST if body.mirror_first is None
                    else bool(body.mirror_first))
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
            # v1.275.4 — THE ANCHOR IS NOW MEASURED BEFORE IT IS TRUSTED.
            # v1.275.2 adopted whatever close-up came back. Measured on
            # clonejoan 2026-08-09: that anchor scored 0.4660 against the
            # uploaded front reference — barely over ARC_MATCH — while the
            # views it produced scored 0.76-0.79 against the ANCHOR and only
            # 0.36-0.39 against the upload. The mechanism worked perfectly and
            # propagated the wrong face. An unmeasured anchor is a drift
            # amplifier: every view inherits its error and adds its own.
            # So: score it, and if it is below the band, spend ONE more render
            # on a different seed and keep the better of the two. The loser is
            # retagged `other` rather than deleted — it is still a picture of
            # roughly this person, it is just not allowed to be the anchor.
            face_path: List[str] = []
            existing = [r for r in _refs_by_tag(c, "face")
                        if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
            if existing and not body.regen_face:
                # v1.275.4b: BEST, not NEWEST. Measured on clonejoan the same
                # afternoon: three anchors scored 0.4660, 0.3499 and 0.3926
                # against the upload, and `existing[-1]` would have reused the
                # 0.3926 one purely because it was last. Newest is not a quality
                # signal; the score is, and it is free.
                best_p, best_sc = None, None
                for r in existing:
                    p = _cdir(slug) / "refs" / f"{r['id']}.png"
                    s = _anchor_score(slug, c, p)
                    if best_p is None or (s is not None and
                                          (best_sc is None or s > best_sc)):
                        best_p, best_sc = p, s
                face_path = [str(best_p)]
                sc = best_sc
                st["anchor_score"] = sc
                st["anchor_source"] = f"reused (best of {len(existing)})"
                if sc is not None and sc < _ANCHOR_MIN:
                    logger.warning(
                        "klein3 %s: REUSED face anchor scores %.4f vs the front "
                        "reference (below %.2f) — every view will inherit that "
                        "drift. Pass regen_face:true to render a fresh one.",
                        slug, sc, _ANCHOR_MIN)
            else:
                total += 1
                st["detail"] = f"0/{total} (face anchor first)"

                def _render_anchor(seed: int) -> Optional[Tuple[str, Optional[float]]]:
                    """One face close-up → (path, score). None if it failed."""
                    got: List[str] = []

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
                        got.append(str(p))

                    try:
                        _parallel_klein_edits(
                            disp,
                            [{"key": "face", "prompt": _face_prompt(fields),
                              "refs": id_refs, "w": 832, "h": 1024, "seed": seed}],
                            on_face, st)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("klein3 face anchor render failed: %s", e)
                        return None
                    if not got:
                        return None
                    return got[0], _anchor_score(slug, _load(slug), Path(got[0]))

                # v1.275.4b: a forced regen competes against the anchors that
                # already exist instead of replacing them blind. Three measured
                # anchors on one character spanned 0.3499-0.4660 — this render
                # is a lottery ticket, not an upgrade, and the best ticket in
                # hand should not be thrown away because a newer one printed.
                best, best_sc, tries = None, None, 0
                for r in existing:
                    p = _cdir(slug) / "refs" / f"{r['id']}.png"
                    s = _anchor_score(slug, c, p)
                    if s is not None and (best_sc is None or s > best_sc):
                        best, best_sc = str(p), s
                fresh: List[Tuple[str, Optional[float]]] = []

                # v1.275.7: try the CROP first. It costs one GAN pass instead of
                # a Klein render and it cannot drift off the upload, so if it
                # clears the bar there is nothing to generate.
                if body.face_from_crop:
                    st["detail"] = f"0/{total} (face crop from upload)"
                    cropped = _face_crop_ref(slug, _load(slug), disp,
                                             gan=body.face_crop_gan)
                    if cropped:
                        cp, csc, cmeta = cropped
                        st["face_crop"] = cmeta
                        logger.info("klein3 %s: face crop scores %s vs the front "
                                    "ref (%s)", slug,
                                    "n/a" if csc is None else f"{csc:.4f}", cmeta)
                        if csc is None or (best_sc is None or csc > best_sc):
                            best, best_sc = cp, csc
                        if best_sc is not None and best_sc >= _ANCHOR_MIN:
                            face_path = [best]
                            st["anchor_score"] = best_sc
                            st["anchor_source"] = (
                                "cropped from upload"
                                + (" (GAN)" if cmeta.get("gan") else ""))
                            st["done"] = st.get("done", []) + ["face"]
                            st["detail"] = f"{len(st['done'])}/{total}"
                crop_won = bool(face_path)   # crop cleared the bar; nothing to render
                first = None if crop_won else _render_anchor(seed0 - 1)
                if first:
                    tries = 1
                    fresh.append(first)
                    if best_sc is None or (first[1] is not None and first[1] > best_sc):
                        best, best_sc = first
                    if best_sc is not None and best_sc < _ANCHOR_MIN:
                        logger.warning(
                            "klein3 %s: best face anchor is %.4f vs the front "
                            "reference (below %.2f) — retrying once on a new seed",
                            slug, best_sc, _ANCHOR_MIN)
                        total += 1
                        second = _render_anchor(seed0 - 2)
                        if second:
                            tries = 2
                            fresh.append(second)
                            if second[1] is not None and (best_sc is None or
                                                          second[1] > best_sc):
                                best, best_sc = second
                # Demote only the close-ups THIS run produced and did not pick.
                # An incumbent anchor that lost is left exactly as it was —
                # rewriting a ref that another run created is not this run's
                # business, and the audit script reads history off these tags.
                losers = [p for p, _ in fresh if p != best]
                if losers:
                    c3 = _load(slug)
                    lids = {Path(p).stem for p in losers}
                    for r in c3.get("refs", []):
                        if r.get("id") in lids:
                            r["tag"] = "other"
                            r["name"] = "face close-up (not chosen as anchor)"
                    _save(slug, c3)
                if not crop_won:
                    if best:
                        face_path = [best]
                    st["anchor_score"] = best_sc
                    st["anchor_source"] = (
                        f"generated ({tries} render(s))"
                        if best in [p for p, _ in fresh]
                        else (f"cropped from upload (beat {tries} render(s))"
                              if best and Path(best).name not in
                              {Path(p).name for p, _ in fresh} and tries
                              else f"kept incumbent (beat {tries} fresh render(s))"))
                    st["done"] = st.get("done", []) + ["face"]
                    st["detail"] = f"{len(st['done'])}/{total}"
                if best_sc is not None and best_sc < _ANCHOR_MIN:
                    logger.warning(
                        "klein3 %s: BEST anchor is still %.4f vs the front "
                        "reference. The view set will be self-consistent around "
                        "a face that is not the uploaded one.", slug, best_sc)
            if face_path:
                # Recompute against the CURRENT char.json: the anchor render may
                # have added or retagged refs, and a stale id_refs list is how a
                # demoted close-up sneaks back in as reference 2.
                # v1.276.14 — THE FACE ANCHOR MUST NOT DISPLACE THE BODY.
                # Lorenzo's side views came back in the wrong trousers, and the
                # anchor is a head-and-shoulders crop: it carries NO information
                # below the collar. Leading with it while the cap trims the list
                # can leave a view job reasoning about a body it was never
                # shown. So the full-body FRONT reference is pinned in
                # explicitly, right behind the face, before anything else
                # competes for the remaining slots.
                c_now = _load(slug)
                cur = _identity_ref_paths(slug, c_now, limit=nref)
                # NB: not `body` — that is the request model in this scope.
                body_ref = _front_ref_path(slug, c_now)
                ordered = [face_path[0]]
                if body_ref is not None and str(body_ref) != face_path[0]:
                    ordered.append(str(body_ref))      # face + BODY, always
                ordered += [p for p in cur if p not in ordered]
                refs_for_views = ordered[:nref]
                # the shared pool BEFORE per-view filtering (v1.276.17) —
                # `refs_used` below is what each job actually received.
                st["refs_pool"] = [Path(p).stem for p in refs_for_views]

        # ── phase 2: the views, face-anchored ───────────────────────────────
        # ⚠ v1.276.17 — PER-VIEW REFERENCE LISTS. Every view used to get the
        # SAME list, and on a character that already had a left view that list
        # was [face, front, LEFT] — so the RIGHT job was handed a left profile
        # and produced a left-facing pose. Lorenzo: "it won't give me the right
        # side view, it keeps giving me the character in a left facing pose."
        # Third instance of one lane being fed a competing view as evidence
        # (v1.275.9 fronts, v1.276.16 outfit renders, now the opposite profile).
        c_ref = _load(slug)
        _rv = {v: _view_ref_paths(slug, c_ref, v, refs_for_views, limit=nref)
               for v in todo}
        refs_by_view = {v: ps for v, (ps, _sl) in _rv.items()}
        angle_slot = {v: sl for v, (_ps, sl) in _rv.items()}
        st["refs_used"] = {v: [Path(p).stem for p in ps]
                           for v, ps in refs_by_view.items()}
        st["angle_ref"] = {v: sl for v, sl in angle_slot.items() if sl}
        def _mk_job(v: str, attempt: int, seed: int) -> dict:
            """One attempt at one view. Attempts after the first may use the
            🪞 MIRROR strategy on side views (see _MIRROR_NOTE)."""
            # v1.276.18: retries ALTERNATE strategies rather than repeating
            # one. Neither route is reliable on its own (measured: plain ~1 in
            # 4 by his count, 🪞 mirror 3 of 4 by mine — both small samples), so
            # three attempts covering both beats three attempts of the better
            # one. attempt 1 = plain, 2 = mirror, 3 = plain, 4 = mirror…
            if v not in ("left", "right"):
                mirror = False
            elif mirror_first:
                mirror = (attempt % 2 == 1)
            else:
                mirror = mirror_retry and (attempt % 2 == 0)
            if not mirror:
                return {"key": v, "prompt": _view_prompt(v, fields,
                                                        angle_slot.get(v, 0)),
                        "refs": refs_by_view[v], "w": w, "h": h, "seed": seed,
                        "view": v, "attempt": attempt, "mirror": False}
            tmp = _cdir(slug) / "_mirror"
            flipped = [str(_flip_png(p, tmp / f"{attempt}_{Path(p).stem}.png"))
                       for p in refs_by_view[v]]
            other = _OPPOSITE_VIEW[v]
            return {"key": v, "prompt": _view_prompt(other, fields,
                                                     angle_slot.get(v, 0)),
                    "refs": flipped, "w": w, "h": h, "seed": seed,
                    "view": v, "attempt": attempt, "mirror": True}

        # ⭐ v1.276.19 — ORDER MATTERS on a fresh character. A side view's
        # direction reference is the OPPOSITE profile mirrored, and a brand-new
        # character has neither side yet, so both would render blind. Render the
        # side the model is naturally good at FIRST, then the other one with the
        # first one mirrored as its direction reference. Costs no extra render —
        # it only serialises one job that used to run in parallel.
        # RIGHT is the one that waits: left is the direction this model reaches
        # for on its own (his 1-in-4 was the right view; the left has never been
        # reported wrong), so left is the cheap one to get first.
        deferred = [v for v in ("right",)
                    if v in todo and not angle_slot.get(v)
                    and _OPPOSITE_VIEW[v] in todo]
        first_pass = [v for v in todo if v not in deferred]
        if deferred:
            st["deferred"] = deferred
        jobs = [_mk_job(v, 1, seed0 + i) for i, v in enumerate(first_pass)]
        st["attempts"] = {v: [] for v in todo}

        def on_result(jb, data):
            v = jb["view"]
            if jb.get("mirror"):
                # Flip the RESULT back. Two flips cancel, so the character's
                # real chirality (hair parting, a scar, which hand holds what)
                # survives — this is not the same as mirroring a finished
                # left view, which would swap all of that.
                from PIL import Image
                import io as _io
                with Image.open(_io.BytesIO(data)) as im:
                    buf = _io.BytesIO()
                    im.transpose(Image.FLIP_LEFT_RIGHT).save(buf, "PNG")
                    data = buf.getvalue()
            rid = uuid4().hex[:12]
            path = _cdir(slug) / "refs" / f"{rid}.png"
            _save_png_bytes(data, path)

            ok, why = (True, "not checked")
            if verify:
                ok, why = _facing_verdict(path, v)
            st["attempts"][v] = st["attempts"].get(v, []) + [
                {"attempt": jb["attempt"], "mirror": bool(jb.get("mirror")),
                 "ok": ok, "why": why, "ref": rid}]

            # ⚠ A view that never passed must NOT be filed as that view.
            # Lorenzo's whole reason for asking: "we won't end up with an
            # incorrect base as that will poison all the other additional tasks
            # in the autogen chain." A MISSING right view is a problem autogen
            # can see and stop on; a wrong-facing one tagged `right` is a
            # problem it silently builds a dataset, a LoRA and a wardrobe on.
            if not ok:
                # Wrong view — do not keep it as a reference (a wrong-facing
                # "right" ref is exactly what poisons the next run) and try
                # again. The reject is kept on disk under `other` so he can
                # see what was rejected rather than being told it happened.
                c2 = _load(slug)
                c2.setdefault("refs", []).append(
                    {"id": rid, "tag": "other",
                     "name": f"REJECTED {v} view — {why}",
                     "source": "generated", "created_at": _now(),
                     "rejected": True, "wanted_view": v})
                _save(slug, c2)
                if jb["attempt"] < max_tries:
                    st["detail"] = (f"{len(st.get('done', []))}/{total} · retrying "
                                    f"{v} ({jb['attempt'] + 1}/{max_tries}): {why}")
                    return _mk_job(v, jb["attempt"] + 1,
                                   jb["seed"] + 7919 * jb["attempt"])
                # out of attempts — leave the view MISSING and say so loudly.
                st["failed"] = st.get("failed", []) + [
                    {"view": v, "tries": max_tries, "why": why}]
                st["detail"] = (f"{len(st.get('done', []))}/{total} · {v} FAILED "
                                f"after {max_tries} tries: {why}")
                return None

            c2 = _load(slug)
            # v1.276.19 — SUPERSEDE, don't stack. Regenerating a view used to
            # APPEND, so clonejoan finished this session with nine `right` refs
            # and the pickers were choosing among them by recency. Older
            # GENERATED views of the same tag are moved to `other` and flagged;
            # they are never deleted, and an UPLOAD or a CROP is never touched —
            # those are source material, not a claim under test.
            for r in c2.get("refs", []):
                if (r.get("tag") == v and r.get("id") != rid
                        and r.get("source") == "generated"):
                    r["tag"] = "other"
                    r["superseded"] = True
                    r["wanted_view"] = v
                    r["name"] = f"superseded {v} view"
            c2.setdefault("refs", []).append(
                {"id": rid, "tag": v, "name": f"generated {v} view",
                 "source": "generated", "created_at": _now(),
                 "verified": ok, "verify_note": why,
                 "attempts": jb["attempt"], "mirrored": bool(jb.get("mirror")),
                 "angle_ref": bool(angle_slot.get(v))})
            _save(slug, c2)
            st["done"] = st.get("done", []) + [v]
            st["detail"] = f"{len(st['done'])}/{total}"
            return None

        try:
            _parallel_klein_edits(disp, jobs, on_result, st)   # fans across workers
            if deferred:
                # second pass: the opposite side now exists, so recompute the
                # reference list — the direction ref appears here.
                c2 = _load(slug)
                for n, v in enumerate(deferred):
                    ps, sl = _view_ref_paths(slug, c2, v, refs_for_views, limit=nref)
                    refs_by_view[v], angle_slot[v] = ps, sl
                    st["refs_used"][v] = [Path(p).stem for p in ps]
                    if sl:
                        st.setdefault("angle_ref", {})[v] = sl
                _parallel_klein_edits(
                    disp, [_mk_job(v, 1, seed0 + 991 + n)
                           for n, v in enumerate(deferred)], on_result, st)
            errs = [f"{k}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            errs += [f"{f['view']}: not produced in {f['tries']} tries — {f['why']}"
                     for f in st.get("failed", [])]
            st["error"] = "; ".join(errs) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "views": todo, "verify": verify,
            "max_tries": max_tries}


# ── 👗 Outfit sets (v1.276.0) ────────────────────────────────────────────────
#
# Lorenzo went to the Clothes tab with a character he had just made in Klein 3.0
# and it was not in the list.  The reason turned out to be architectural: that
# tab dresses a `StudioCharacter` row using sprite shards that live on a VNCCS
# worker, and a Klein 3.0 character is a folder of tagged images on this machine
# with no DB row at all.  Bending that route to accept a slug would have meant
# threading a second identity type through the asset loader, the sprite picker
# and the costume writer — a lot of blast radius around a path that currently
# works for VNCCS characters.
#
# So Klein 3.0 dresses characters the Klein 3.0 way, with the machinery this
# mode already has and that is already proven: an outfit is a Klein edit of a
# view reference, fanned across the workers by `_parallel_klein_edits`, saved
# back as an `outfit`-tagged ref.  A SET is that same edit applied to every
# view, so a costume comes out consistent from the front, back and both sides.
#
# Affirmative prompts only, and every garment NAMED — Klein has no negative
# prompt node and runs at cfg=1, so "no jacket" injects a jacket, and category
# words like "clothing" are ignored.

# v1.276.2 — the full wardrobe vocabulary. Lorenzo: "maximize the ability to
# create outfits — if people want to do it simply they can, but if they want
# more detail, this gives them the avenue."
#
# So: FOUR core slots cover the simple case (top, bottom, shoes, outerwear) and
# nine more cover everything else. Every slot is optional; empty ones are
# skipped entirely rather than emitting "no hat", which at cfg=1 with no
# negative node would put a hat on the character.
#
# `group` drives the UI: "core" renders expanded, "more" behind a disclosure.
# ORDER IS THE PROMPT ORDER — head to toe, then held items — because the list
# is comma-joined into one sentence and it should read like a person describing
# what they can see. The original six keys are all preserved unchanged, so
# outfits saved before this still load and re-render identically.
_OUTFIT_SLOT_META = [
    {"key": "headwear", "label": "Headwear", "group": "more",
     "example": "a black wool beanie"},
    {"key": "eyewear", "label": "Eyewear", "group": "more",
     "example": "thin gold wire-frame glasses"},
    {"key": "outerwear", "label": "Outerwear", "group": "core",
     "example": "a cropped red leather biker jacket with silver zips"},
    {"key": "top", "label": "Top", "group": "core",
     "example": "a plain white ribbed tank top"},
    {"key": "underlayer", "label": "Base layer / underwear", "group": "more",
     "example": "a grey cotton long-sleeve undershirt"},
    {"key": "belt", "label": "Belt", "group": "more",
     "example": "a brown leather belt with a brass buckle"},
    {"key": "bottom", "label": "Bottom", "group": "core",
     "example": "black slim-fit jeans"},
    {"key": "legwear", "label": "Legwear", "group": "more",
     "example": "sheer black tights"},
    {"key": "shoes", "label": "Shoes", "group": "core",
     "example": "black leather ankle boots"},
    {"key": "gloves", "label": "Gloves", "group": "more",
     "example": "fingerless black leather gloves"},
    {"key": "jewellery", "label": "Jewellery", "group": "more",
     "example": "small silver hoop earrings and a thin cross necklace"},
    {"key": "accessories", "label": "Other accessories", "group": "more",
     "example": "a charcoal wool scarf"},
    {"key": "carried", "label": "Carried / held", "group": "more",
     "example": "a worn brown leather satchel"},
]
_OUTFIT_SLOTS = tuple(s["key"] for s in _OUTFIT_SLOT_META)
#: v1.276.21 — an outfit renders the four body views PLUS a face close-up.
#: Lorenzo: "we should also do a closeup face render as well for outfits given
#: if we add jewelry or anything." Earrings, a necklace, glasses, a hat brim and
#: a collar are all decided at head height and are a handful of pixels in an
#: 832×1216 full body — the one thing a wardrobe most needs to show is the one
#: thing the full-body views cannot.
_OUTFIT_VIEWS = tuple(VIEW_TAGS) + ("face",)
#: What is actually visible in a head-and-shoulders crop. Naming a garment that
#: cannot be seen is not neutral at cfg=1 — it invites the model to pull it into
#: frame, which is how you get boots in a portrait.
_FACE_VISIBLE_SLOTS = ("headwear", "eyewear", "jewellery", "accessories",
                       "outerwear", "top", "underlayer")
# Held, not worn — it gets its own clause so the sentence stays true.
_CARRIED_SLOTS = ("carried",)


def _outfit_prompt(slots: Dict[str, str], extra: str, fields: Dict[str, Any],
                   view: str = "front", garment_slot: int = 0,
                   face_slot: int = 0, outfit_slot: int = 0,
                   styled_face: bool = False, sibling_slot: int = 0) -> str:
    """Name every garment, affirmatively, and pin everything else in place.

    Slots are emitted in _OUTFIT_SLOT_META order (head to toe), NOT in whatever
    order the caller's dict happens to iterate — the prompt is one sentence and
    it should read like a description, not a form dump.

    ⚠ v1.276.16 — THE SAME CATEGORY-WORD BUG, THIRD TIME. This prompt was built
    ONCE for the whole set and said "identical standing pose, CAMERA ANGLE and
    framing". "camera angle" is a category word: Klein does not read it, exactly
    as it did not read "outfit" in `_view_prompt` (v1.276.14) or "clothing" in
    `_klein_prompt` (2026-08-04). So a left-side job got its own left image as
    reference 1, two FRONT-facing identity refs behind it, and no words naming
    the facing — and came back frontal. Lorenzo: "many of the views are
    identical or don't work… the position of the character is off."

    Now the prompt is built PER VIEW and NAMES the facing with the same
    `_VIEW_PROMPTS` vocabulary that the base view set uses.
    """
    worn = [str(slots.get(k) or "").strip() for k in _OUTFIT_SLOTS
            if k not in _CARRIED_SLOTS and str(slots.get(k) or "").strip()]
    held = [str(slots.get(k) or "").strip() for k in _CARRIED_SLOTS
            if str(slots.get(k) or "").strip()]
    if extra.strip():
        worn.append(extra.strip())
    if view == "back":
        worn = _back_garments(worn)
    garments = ", ".join(worn) if worn else "a plain fitted t-shirt and plain trousers"
    carry = f", and carrying {', '.join(held)}" if held else ""
    hair = str(fields.get("hair") or "").strip()
    keep_hair = f", identical {hair} hairstyle" if hair else ", identical hairstyle"
    # v1.276.20 — reference 1 is a GENERATED view, so its face is already a copy.
    # Point at the face crop by SLOT NUMBER so identity is taken from the sharp
    # close-up rather than averaged out of the thing being edited.
    face_clause = ""
    if face_slot and styled_face:
        # image {face_slot} is this OUTFIT's own close-up, so it carries the
        # head-height styling as well as the face — say so, or the model treats
        # it as identity only and re-invents the earrings at 40 pixels.
        face_clause = (f" Her face is exactly the face in image {face_slot} — "
                       f"the same features, the same bone structure, the same "
                       f"eyes and the same skin — and she wears exactly the "
                       f"same earrings, necklace, collar and neckline as in "
                       f"image {face_slot}; take the face and the jewellery "
                       f"from image {face_slot}, not from image 1.")
    elif face_slot:
        face_clause = (f" Her face is exactly the face in image {face_slot} — "
                       f"the same features, the same bone structure, the same "
                       f"eyes and the same skin; take the face from image "
                       f"{face_slot}, not from image 1.")
    # v1.276.21 — the already-rendered FRONT view of this same outfit, cited so
    # the close-up shows the garments that were actually produced rather than a
    # second independent interpretation of the same words.
    outfit_clause = ""
    if outfit_slot:
        outfit_clause = (f" She is wearing exactly the same garments and the "
                         f"same jewellery as in image {outfit_slot} — identical "
                         f"items in identical colours.")
    if view == "face":
        # A close-up is not "the same prompt, zoomed". Only head-height items
        # are named (see _FACE_VISIBLE_SLOTS) and the framing is stated
        # explicitly, because the reference is a full-body view and Klein will
        # otherwise reproduce its framing along with everything else.
        seen = [str(slots.get(k) or "").strip() for k in _FACE_VISIBLE_SLOTS
                if str(slots.get(k) or "").strip()]
        worn_face = ", ".join(seen) if seen else garments
        return ("A zoomed-in close-up PORTRAIT of the exact same person — head "
                "and shoulders only, the face filling most of the frame, "
                "looking straight at the camera with a neutral expression, "
                f"SAME face, SAME eyes{keep_hair} and SAME skin tone as the "
                f"reference images, wearing {worn_face}, every detail of the "
                "jewellery, eyewear, headwear and collar sharp and clearly "
                "visible, sharp focus on the eyes, plain white studio "
                f"background, even lighting, photorealistic.{face_clause}"
                f"{outfit_clause}")
    sib_clause = ""
    if sibling_slot:
        sib_clause = (f" Image {sibling_slot} is this same costume photographed "
                      f"from the other side and already turned to face the same "
                      f"way: match it exactly — the same garments in the same "
                      f"colours, with the same trims, seams, fastenings and "
                      f"hemlines as image {sibling_slot}.")
    if view == "back":
        # ⚠ v1.276.24 — Lorenzo: "the back view is the character facing the
        # right way but the costume is backwards." He is right and the cause is
        # in the words: the garment list says "a shield emblem ON THE CHEST",
        # and from behind the chest is not visible — so the model puts the
        # emblem where it CAN be seen. Nothing in the prompt said the front of
        # the costume is facing away.
        #
        # The fix is affirmative (it has to be — "no emblem on the back" would
        # paint one there): state where the front detailing actually IS, and
        # describe the back panels positively as the thing in view.
        back_clause = (
            " Only the back panels of the costume are in view: the back of the "
            "outer layer, the plain unbroken back panel of the top, the back of "
            "the lower garment and the heels of the shoes.")
    else:
        back_clause = ""
    facing = _VIEW_PROMPTS.get(view, _VIEW_PROMPTS["front"])
    # ⚠ Klein addresses references POSITIONALLY — "image 2", never "the garment
    # photo" (feedback_klein_reference_syntax). If a garment photo is in the
    # list, the prompt has to point at its slot number or it is just another
    # picture Klein averages in.
    if garment_slot:
        # v1.276.28: "ONLY the garments" and "she stands on her own feet" are
        # both affirmative. A costume reference is photographed on a mannequin,
        # and whatever else is in that picture — a stand, a plinth, a hanger —
        # is copied along with the clothes unless the prompt gives the render
        # somewhere else to put the weight.
        garments = (f"{garments} — ONLY the garments shown in image "
                    f"{garment_slot}, the same cut, the same colour, the same "
                    f"fabric and the same fastenings, worn by her and fitted to "
                    f"her own body while she stands on her own two feet on a "
                    f"clean empty floor")
    return (f"The exact same person from image 1, full body shot {facing}, "
            f"standing straight with arms relaxed at the sides — identical face, "
            f"identical body proportions{keep_hair}, in the exact same standing "
            f"position and the exact same distance from the camera as image 1 — "
            f"now wearing {garments}{carry}. The clothing fits naturally with "
            "realistic fabric folds, seams and drape. Plain white studio "
            f"background, even lighting, photorealistic full body shot."
            f"{back_clause}{sib_clause}{face_clause}")


#: For a dressed SIDE view, a frontal full-body reference is not neutral — it is
#: a competing composition, and Klein splits the difference (or picks the front).
#: v1.276.16: an outfit job's refs are chosen RELATIVE TO ITS TARGET VIEW. The
#: face crop carries identity without carrying a full-body facing, so it comes
#: first; the OPPOSITE profile is dropped outright. Unmeasured on the GPU as of
#: writing — the naming fix above is the part with precedent behind it.
_OPPOSITE_VIEW = {"left": "right", "right": "left", "front": "back", "back": "front"}


def _outfit_ref_paths(slug: str, c: dict, view: str, own: str,
                      limit: int = 3) -> Tuple[List[str], int]:
    """Reference list for dressing ONE view, plus the FACE slot (1-based, or 0).

    `own` (that view's own image) is always reference 1 — it carries the pose,
    framing and facing.

    ⚠ v1.276.20 — THE FACE IS PINNED AND CITED. Lorenzo: "we seem to get some
    face likeness drift when doing the outfit renders. are we including the
    face reference image to ensure max likeness? The view we are trying to use
    for reference won't be enough to dial in the face." He is right on both
    counts, and the second half is the important one:

      · reference 1 is a GENERATED view, which already sits around 0.33–0.41
        against the upload — so an outfit edit that takes its face from image 1
        is copying a copy, and the error compounds;
      · the face crop WAS in the list, but only by tag ORDER, and it was never
        mentioned in the prompt. Klein addresses references POSITIONALLY, so an
        uncited reference is just something it averages in. A garment photo
        could also displace it.

    So the face crop is now pinned to slot 2 for every view that has a face in
    it, and `_outfit_prompt` names that slot number explicitly."""
    def _pick(tag: str) -> Optional[str]:
        cands = [r for r in _refs_by_tag(c, tag)
                 if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
        if not cands:
            return None
        ups = [r for r in cands if r.get("source") == "upload"]
        crops = [r for r in cands if r.get("source") == "crop"]
        r = (ups or crops or cands)[-1]
        return str(_cdir(slug) / "refs" / f"{r['id']}.png")

    # face first (identity, no competing body), then the target view's own tag,
    # then the front, then anything left — minus the opposite profile.
    #
    # ⚠ `outfit`-tagged refs are NEVER identity references here. They are this
    # app's own earlier renders of the exact image being replaced, and on the
    # run that found this every one of them was frontal — so the LEFT job was
    # handed a frontal picture as evidence of what "left" looks like. That is
    # the v1.275.9 drift loop wearing a jacket.
    if view == "back":
        # No face is visible in a back view, so a face close-up is not identity
        # evidence here — it is an instruction to turn around. Same for a front
        # full-body. What matters is that the garment reads the same from behind.
        order = [view, "other", "left", "right"]
    else:
        # For a SIDE view the front full-body is demoted behind everything else:
        # it is a competing composition, not neutral identity evidence. It is
        # still there as a fallback, because on a fresh character it may be the
        # only other face-bearing image that exists.
        order = ["face", view, "other", "front"]
        # anything still unused, minus the opposite profile (it fights the
        # facing) — `back` sorts last everywhere because it carries no face.
        order += [v for v in VIEW_TAGS
                  if v not in order and v != _OPPOSITE_VIEW.get(view)
                  and v != "back"]
        order.append("back")
    out = [own]
    face_slot = 0
    if view != "back":
        fp = _pick("face")
        if fp and fp != own:
            out.append(fp)              # PINNED at slot 2, then cited by number
            face_slot = 2
    for tag in order:
        p = _pick(tag)
        if p and p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out[:limit], (face_slot if face_slot <= limit else 0)


class OutfitIn(BaseModel):
    name: str                              # what to call this outfit
    variant: str = ""                      # v1.276.2: a LOOK within the outfit —
                                           # "jacket off", "sleeves rolled". Empty
                                           # = the base look. Same wardrobe, one
                                           # change; a scene where she takes the
                                           # jacket off should not need a whole
                                           # second outfit.
    slots: Dict[str, str] = {}             # see _OUTFIT_SLOT_META (13 slots)
    extra: str = ""                        # free text appended to the garments
    garment_ref: Optional[str] = None      # v1.276.17: a `garment`-tagged ref —
                                           # the PHOTO the outfit came from,
                                           # passed to the render as reference 2
                                           # so Klein copies the actual cut and
                                           # hardware, not a paraphrase of them.
    views: List[str] = []                  # [] = the whole SET (front/back/left/right)
    verify: bool = True                    # v1.276.22: vision-check each finished
                                           # view against the garment list and
                                           # re-render it if items are missing,
                                           # miscoloured or — the one that bit
                                           # him — PRESENT BUT NEVER ASKED FOR.
    max_tries: int = 2                     # attempts per view including the first
    upscale_front_first: bool = True       # ⭐ v1.276.37: upscale the FRONT
                                           # render before cropping the face out
                                           # of it, so the crop starts from real
                                           # detail instead of ~180px.
    face_from_front: bool = True           # ⭐ v1.276.29: the 🙂 close-up is a
                                           # CROP of this outfit's own FRONT
                                           # render (then upscaled), not a fresh
                                           # generation. It CANNOT disagree with
                                           # the costume, because it IS the
                                           # costume. Lorenzo's idea, and better
                                           # than what was here.
    sibling_ref: bool = True               # v1.276.26: give each SIDE view the
                                           # other side's finished render,
                                           # mirrored, as garment evidence.
                                           # Exposed so it can be A/B'd — it was.
    only_missing: bool = False             # v1.276.16: render ONLY the views this
                                           # (name, variant) has no image for.
                                           # Lorenzo: "regenerate the ones missing
                                           # that we want to regenerate at the
                                           # same time" — the counterpart to
                                           # deleting one bad view and refilling
                                           # it without touching the good ones.
    seed: Optional[int] = None
    width: int = 832
    height: int = 1216
    ref_count: int = 3


@router.get("/characters/{slug}/outfits")
async def outfits_list(slug: str):
    """Every outfit → its variants → their per-view images, newest first.

    Three levels because that is what the thing actually is: an OUTFIT is a
    wardrobe entry, a VARIANT is one look within it (jacket on / jacket off),
    and each variant has one standalone image per view.
    """
    c = _load(slug)
    groups: Dict[str, dict] = {}
    for r in c.get("refs", []):
        if r.get("tag") != "outfit":
            continue
        o = r.get("outfit") or {}
        nm = str(o.get("name") or r.get("name") or "outfit")
        vr = str(o.get("variant") or "")
        g = groups.setdefault(nm, {"name": nm, "variants": {},
                                   "created_at": r.get("created_at")})
        gref = o.get("garment_ref") or None
        v = g["variants"].setdefault(vr, {
            "variant": vr, "label": vr or "base look",
            "slots": o.get("slots") or {}, "extra": o.get("extra") or "",
            "garment_ref": gref,
            "garment_url": _ref_url(slug, gref) if gref else None,
            "views": {}, "created_at": r.get("created_at"),
        })
        v["views"][str(o.get("view") or "front")] = {
            "id": r["id"],
            "built_from": [{"id": x, "url": _ref_url(slug, x)}
                           for x in (o.get("built_from") or [])],
            "url": _ref_url(slug, r["id"]),
            "download_url": _ref_url(slug, r["id"], download=True),
            "created_at": r.get("created_at"),
        }
        if (r.get("created_at") or "") > (g.get("created_at") or ""):
            g["created_at"] = r.get("created_at")
    out = []
    for g in groups.values():
        # base look first, then variants newest-first — the base is the thing
        # the others are a change TO, so it reads wrong anywhere else.
        vs = sorted(g["variants"].values(),
                    key=lambda v: (v["variant"] != "", v.get("created_at") or ""))
        out.append({"name": g["name"], "created_at": g["created_at"], "variants": vs})
    out.sort(key=lambda g: g.get("created_at") or "", reverse=True)
    return {"slug": slug, "outfits": out,
            "slots": _OUTFIT_SLOT_META, "slot_keys": list(_OUTFIT_SLOTS)}


# ── 👗 Outfit from a PHOTO: vision scan → named slots (v1.276.17) ────────────
# Lorenzo: "we need the ability to use an image reference for the clothing and
# the llm vision scan to describe it. So if we create a new outfit we should be
# able to base it off an image like the hat in the picture or outfit in the
# picture on the character."
#
# Two-stage on purpose, and he chose both stages:
#   1. the vision model NAMES the garments into the 13 slots — editable text,
#      correctable before a single render is spent, and reusable on any
#      character;
#   2. the photo itself rides along as a render reference so Klein can copy the
#      actual cut, fabric and hardware rather than a description of them.
_GARMENT_SYSTEM = (
    "You are a costume supervisor cataloguing a garment for a photo shoot. "
    "You describe only what is visibly present. You never invent items."
)
_GARMENT_PROMPT = (
    "Look at this image and list ONLY the clothing, footwear and worn or "
    "carried accessories you can actually see.\n\n"
    "Return STRICT JSON with these keys — omit any key whose item is not "
    "visible, and never write \"none\", \"no hat\" or an empty description:\n"
    "  headwear, eyewear, outerwear, top, underlayer, belt, bottom, legwear, "
    "shoes, gloves, jewellery, accessories, carried\n\n"
    "Each value is a short noun phrase naming the item with its COLOUR, "
    "MATERIAL and any distinctive detail — for example "
    "\"a cropped red leather biker jacket with silver zips\" or "
    "\"a black wool beanie with a folded brim\". "
    "Describe the garments only: say nothing about the person, their pose, "
    "their body, the background or the lighting."
)


def _parse_garment_json(text: str) -> Dict[str, str]:
    """Pull the slot dict out of a vision reply. Never raises.

    Only keys in _OUTFIT_SLOTS survive, and the negative answers the model
    produces anyway ("none", "no hat", "not visible") are dropped — at cfg=1
    with no negative node, writing "no hat" into a prompt puts a hat on."""
    raw = str(text or "").strip()
    if not raw:
        return {}
    if "{" in raw:                       # tolerate ```json fences / prose
        raw = raw[raw.index("{"): raw.rindex("}") + 1] if "}" in raw else raw
    try:
        data = json.loads(raw)
    except Exception:                    # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    bad = ("none", "n/a", "na", "null", "not visible", "not shown", "unknown",
           "no ", "nothing")
    out: Dict[str, str] = {}
    for k in _OUTFIT_SLOTS:
        v = str(data.get(k) or "").strip().strip('"')
        if not v or v.lower() in bad or v.lower().startswith(("no ", "none")):
            continue
        out[k] = v[:200]
    return out


@router.post("/characters/{slug}/outfits/scan")
async def outfit_scan(slug: str,
                      request: Request,
                      file: UploadFile = File(...),
                      keep: str = Form(""),
                      min_side: int = Form(_REF_MIN_SIDE),
                      upscale: bool = Form(True),
                      session: AsyncSession = Depends(get_session)):
    """Describe the clothing in an uploaded photo into the 13 outfit slots.

    `keep` optionally narrows it — "just the hat" — because a photo of a person
    in a full outfit is often shown for one item in it.
    """
    _load(slug)                                    # 404 early if unknown
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    from backend.api.vnccs_native import _ollama_cfg
    urls, _text_model, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings → Ollama vision model).")

    rid = uuid4().hex[:12]
    try:
        _save_png_bytes(raw, _cdir(slug) / "refs" / f"{rid}.png")
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(400, f"unreadable image: {e}")
    c = _load(slug)
    rec = {"id": rid, "tag": "garment",
           "name": file.filename or f"garment {rid}",
           "source": "upload", "created_at": _now()}
    c.setdefault("refs", []).append(rec)
    _save(slug, c)

    # ⭐ v1.276.22 — A SMALL REFERENCE IS A WEAK REFERENCE. Lorenzo: "maybe check
    # the size of the reference, and if its smaller than is optimal, we upscale
    # it so it is a far better reference. the seedvr upscaler does like miracles
    # from what I've seen in this process." Every reference is scaled to ~1MP
    # before it reaches Klein, so a 400px web grab is being scaled UP by the
    # graph with no detail to work from — the buttons, the weave and the trim
    # simply are not in the file. SeedVR2 restores rather than sharpens, which
    # is exactly the right tool. Fired ONLY when the image is genuinely small.
    size = _image_size(_cdir(slug) / "refs" / f"{rid}.png")
    up_note = None
    if size and upscale and min(size) < max(64, int(min_side or _REF_MIN_SIDE)):
        try:
            _start_ref_upscale(slug, rid, _dispatcher(request),
                               engine="auto", max_side=2048)
            up_note = (f"{size[0]}×{size[1]} is under {min_side}px — upscaling it "
                       f"in the background so it works as a reference")
        except Exception as e:                     # noqa: BLE001
            logger.warning("klein3 garment auto-upscale failed: %s", e)
            up_note = f"{size[0]}×{size[1]} is small, and the upscale could not start"

    prompt = _GARMENT_PROMPT
    focus = (keep or "").strip()
    if focus:
        prompt += (f"\n\nIMPORTANT: this image was supplied for one thing in "
                   f"particular — {focus}. Describe that item in full detail "
                   f"and leave every other key out.")
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    try:
        out = await asyncio.to_thread(
            _wiz.ollama_chat_sync, urls, vision_model, _GARMENT_SYSTEM, prompt,
            [_wiz.image_bytes_to_b64(raw)], 0.2, 180.0, True)
    except Exception as e:                         # noqa: BLE001
        logger.warning("klein3 garment scan failed: %s", e)
        out = None
    slots = _parse_garment_json(out or "")
    return {"ref": rid, "tag": "garment",
            "url": _ref_url(slug, rid),
            "slots": slots, "model": vision_model,
            "size": list(size) if size else None, "upscaling": up_note,
            "warning": None if slots else
                       "the vision model returned nothing usable — the image is "
                       "saved and can still be used as a render reference"}


# ── 👗 Did the render actually produce the OUTFIT we asked for? (v1.276.22) ──
# Lorenzo: "verify that the views came out correctly with outfits as well…
# in one instance it kept adding glasses to the character. very interesting, but
# I would like to have it verify against the original clothing references. For
# this example it was a supergirl costume. it got the cape color wrong and the
# glasses were never in the source."
#
# Two DIFFERENT failure modes in one report, and only one of them is the kind a
# facing check would catch:
#   · WRONG  — the cape is the wrong colour (an item that exists, rendered wrong)
#   · EXTRA  — glasses that were never asked for (an item that should not exist)
# The second is the dangerous one for a wardrobe, because nothing in the request
# mentions it, so no amount of re-reading the prompt finds it. It has to be seen.
_OUTFIT_CHECK_SYSTEM = (
    "You are a continuity supervisor on a photo shoot. You compare what is in "
    "the photograph against the costume list you were given. You are precise "
    "about colour and you never overlook an item that is present but unlisted."
)


#: Detailing that lives on the FRONT of a garment and cannot be seen from behind.
_FRONT_DETAIL = ("emblem", "logo", "print", "shield", "crest", "badge", "graphic",
                 "monogram", "motif", "chest", "front", "zip", "zipper", "button",
                 "buckle", "pocket", "lapel", "collar", "neckline", "v-neck",
                 "placket", "tie", "bow", "brooch", "insignia")
_SPLIT_ON = (" with ", " featuring ", " bearing ", " showing ", " that has ",
             " which has ", " emblazoned ")


def _back_garments(worn: List[str]) -> List[str]:
    """Strip FRONT-ONLY detailing out of garment descriptions for a BACK view.

    ⚠ v1.276.24, and it is the franchise-name lesson again. The back view kept
    rendering the costume backwards — the chest emblem printed across her back.
    A trailing clause saying the emblem "is on the front, turned away from the
    camera" did NOT fix it: the garment list still SAID "a blue leotard with a
    red and yellow diamond shield emblem on the chest", and Klein renders what
    is named, positioning it wherever it can be seen.

    So for the back view the detail is not contradicted, it is simply not
    mentioned — "a blue long-sleeved leotard". Describe what is in view; do not
    name what is not.
    """
    out = []
    for g in worn:
        t = g
        for sep in _SPLIT_ON:
            if sep in t.lower():
                head, _, tail = t.lower().partition(sep)
                if any(w in tail for w in _FRONT_DETAIL):
                    t = t[:len(head)].rstrip(" ,")
        # also drop a trailing comma-clause that is purely front detailing
        if "," in t:
            head, _, tail = t.rpartition(",")
            if tail.strip() and any(w in tail.lower() for w in _FRONT_DETAIL):
                t = head.rstrip(" ,")
        out.append(t.strip() or g)
    return out


def _outfit_expected(slots: Dict[str, str], extra: str, view: str = "front") -> str:
    """The costume as a plain checklist, for the vision model to judge against.
    Head-to-toe like the prompt, one item per line so nothing is glossed over.

    ⚠ view-aware: a BACK view is judged against the BACK-stripped list, or the
    checker reports the chest emblem as "missing" from a picture of someone's
    back and burns a retry proving it. A FACE close-up is judged only on what a
    head-and-shoulders crop can contain, for the same reason."""
    keys = list(_OUTFIT_SLOTS)
    if view == "face":
        keys = [k for k in keys if k in _FACE_VISIBLE_SLOTS]
    items = [str(slots.get(k) or "").strip() for k in keys
             if str(slots.get(k) or "").strip()]
    if view == "back":
        items = _back_garments(items)
    if extra.strip() and view != "face":
        items.append(extra.strip())
    return ("\n".join(f"- {t}" for t in items) if items
            else "- plain unremarkable clothing")


def _outfit_check_prompt(expected: str) -> str:
    return (
        "This photograph is supposed to show a person wearing EXACTLY this "
        f"costume and nothing else:\n\n{expected}\n\n"
        "Look at the photograph and answer with STRICT JSON:\n"
        '{"missing": [...], "extra": [...], "wrong_colour": '
        '[{"item": "...", "expected": "...", "seen": "..."}]}\n\n'
        "  missing      — listed items you cannot see in the photograph\n"
        "  extra        — items VISIBLY WORN OR CARRIED in the photograph that "
        "are NOT on the list: glasses, a hat, gloves, a scarf, jewellery, a bag. "
        "Be strict here; an unlisted item is an error even if it suits the look.\n"
        "  wrong_colour — listed items whose colour in the photograph differs "
        "from the colour on the list\n\n"
        "Ignore the background, the lighting, the pose and the person's body. "
        "Judge only the clothing, footwear and worn or carried accessories. "
        "Return empty lists if everything matches."
    )


#: Turning a NEGATIVE finding into an AFFIRMATIVE instruction. Klein has no
#: negative-prompt node and runs at cfg=1, so "no glasses" puts glasses on —
#: this repo measured that on 2026-08-04 and it is the oldest rule in the lane.
#: The counter to an unwanted item is to describe the correct state of the part
#: of the body it occupies, positively.
_EXTRA_FIXES = {
    "glasses": "her bare eyes and eyebrows fully visible and unobstructed",
    "sunglasses": "her bare eyes and eyebrows fully visible and unobstructed",
    "eyewear": "her bare eyes and eyebrows fully visible and unobstructed",
    "spectacles": "her bare eyes and eyebrows fully visible and unobstructed",
    "hat": "her bare hair and the whole top of her head visible",
    "cap": "her bare hair and the whole top of her head visible",
    "headband": "her bare hair and the whole top of her head visible",
    "beanie": "her bare hair and the whole top of her head visible",
    "hood": "her bare hair and the whole top of her head visible",
    "helmet": "her bare hair and the whole top of her head visible",
    "gloves": "her bare hands and bare fingers",
    "scarf": "her bare neck and the collarbone visible",
    "necklace": "her bare neck and the collarbone visible",
    "earrings": "her bare earlobes",
    "mask": "her whole bare face visible",
    "belt": "an uninterrupted waistline",
    # ⚠ these read "hands empty, shoulders clear" and NOT "nothing hanging from
    # her shoulder" — the earlier draft used the latter and it names the very
    # object it is trying to displace, which at cfg=1 is how you get the bag.
    "bag": "both hands open and empty, her shoulders and back clear",
    "backpack": "both hands open and empty, her shoulders and back clear",
    "purse": "both hands open and empty, her shoulders and back clear",
    "watch": "both bare wrists",
    "bracelet": "both bare wrists",
    "tights": "her bare legs",
    "stockings": "her bare legs",
    "socks": "her bare ankles",
}


def _affirmative_fix(item: str) -> str:
    """Positive phrasing that displaces `item`, or '' if we have no good one."""
    low = item.lower()
    for key, fix in _EXTRA_FIXES.items():
        if key in low:
            return fix
    return ""


#: ⭐ MEASURED 2026-08-10, and it is the actual cause of Lorenzo's glasses.
#: A FRANCHISE OR CHARACTER NAME inside a garment slot drags that character's
#: whole costume in with it, accessories included. "a blue supergirl leotard"
#: produced heavy black Clark-Kent glasses on 5 of 5 renders — and NO amount of
#: affirmative correction removed them (0/3 appended, 0/3 in leading position).
#: Removing the single word "supergirl" and describing the same garment
#: literally — "a blue long-sleeved leotard with a red and yellow diamond shield
#: emblem" — produced NO glasses at the SAME SEED, first attempt, check clean.
#: The name was the cause; the correction clause was treating a symptom.
_FRANCHISE_HINT = (" — ⚠ a character or franchise NAME in a garment slot brings "
                   "that character's accessories with it (measured: "
                   "\"supergirl leotard\" adds glasses 5/5, and corrections do "
                   "not remove them). Describe the garment literally instead.")
#: Words that are almost always a franchise rather than a garment.
_NAMEY = ("supergirl", "superman", "batman", "batgirl", "spiderman", "spider-man",
          "wonder woman", "harley quinn", "catwoman", "iron man", "captain america",
          "wolverine", "deadpool", "jedi", "sith", "stormtrooper", "hogwarts",
          "gryffindor", "sailor moon", "naruto", "goku", "mario", "zelda", "link",
          "elsa", "cosplay of", "costume of")


def _name_hint(slots: Dict[str, str], findings: dict) -> str:
    """Append the franchise warning when an EXTRA item shows up AND a slot names
    a character. Only then — an unconditional lecture would be noise."""
    if not (findings.get("extra") or []):
        return ""
    blob = " ".join(str(v or "").lower() for v in slots.values())
    return _FRANCHISE_HINT if any(n in blob for n in _NAMEY) else ""


def _outfit_verdict(urls, model: str, png_path: str | Path,
                    expected: str) -> Tuple[bool, str, dict]:
    """(ok, human summary, raw findings). Never raises; an unusable answer is
    reported as OK, because spending a render on a maybe is worse than keeping
    the image — the same rule the facing verifier uses."""
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    try:
        raw = Path(png_path).read_bytes()
        out = _wiz.ollama_chat_sync(urls, model, _OUTFIT_CHECK_SYSTEM,
                                    _outfit_check_prompt(expected),
                                    [_wiz.image_bytes_to_b64(raw)], 0.1, 180.0, True)
    except Exception as e:                       # noqa: BLE001
        logger.warning("klein3 outfit check failed: %s", e)
        return True, "not checked (vision call failed)", {}
    txt = str(out or "").strip()
    if "{" in txt and "}" in txt:
        txt = txt[txt.index("{"): txt.rindex("}") + 1]
    try:
        data = json.loads(txt)
    except Exception:                            # noqa: BLE001
        return True, "not checked (unreadable reply)", {}
    if not isinstance(data, dict):
        return True, "not checked (unreadable reply)", {}

    def _lst(k):
        v = data.get(k) or []
        return [str(x).strip() for x in v if str(x).strip()][:6] if isinstance(v, list) else []

    missing, extra = _lst("missing"), _lst("extra")
    wrong = []
    wv = data.get("wrong_colour") or data.get("wrong_color") or []
    if isinstance(wv, list):
        for w in wv[:6]:
            if isinstance(w, dict) and str(w.get("item") or "").strip():
                wrong.append({"item": str(w["item"]).strip(),
                              "expected": str(w.get("expected") or "").strip(),
                              "seen": str(w.get("seen") or "").strip()})
    findings = {"missing": missing, "extra": extra, "wrong_colour": wrong}
    bits = []
    if extra:
        bits.append("EXTRA not in the outfit: " + ", ".join(extra))
    if wrong:
        bits.append("wrong colour: " + ", ".join(
            f"{w['item']} is {w['seen'] or '?'}, should be {w['expected'] or '?'}"
            for w in wrong))
    if missing:
        bits.append("missing: " + ", ".join(missing))
    if not bits:
        return True, "matches the outfit", findings
    return False, " · ".join(bits), findings


def _correction_clause(findings: dict) -> str:
    """An AFFIRMATIVE re-render instruction built from the findings.

    ⚠ Every phrase here is positive. "Remove the glasses" and "no glasses" both
    inject glasses at cfg=1 with no negative node."""
    parts = []
    for item in findings.get("extra") or []:
        fix = _affirmative_fix(item)
        if fix:
            parts.append(fix)
    for w in findings.get("wrong_colour") or []:
        if w.get("expected"):
            parts.append(f"the {w['item']} is {w['expected']}")
    for item in findings.get("missing") or []:
        parts.append(f"she is clearly wearing {item}")
    if not parts:
        return ""
    return (" Important, and each of these is clearly visible in the picture: "
            + "; ".join(parts) + ".")


class OutfitUpdateIn(BaseModel):
    """Edit an outfit's TEXT without rendering anything (v1.276.17).

    Lorenzo asked for a Save button that is not a Generate button: "there
    should also be a save button to change information and save it for the
    outfit". So this touches metadata only — no worker is contacted, no image
    changes. A rename MOVES the existing renders onto the new name, because
    that is what renaming a thing means."""
    name: str
    variant: str = ""
    new_name: Optional[str] = None
    new_variant: Optional[str] = None
    slots: Optional[Dict[str, str]] = None
    extra: Optional[str] = None
    garment_ref: Optional[str] = None


@router.post("/characters/{slug}/outfits/update")
async def outfit_update(slug: str, body: OutfitUpdateIn):
    c = _load(slug)
    name = (body.name or "").strip()
    variant = (body.variant or "").strip()
    if not name:
        raise HTTPException(400, "outfit name required")
    new_name = (body.new_name if body.new_name is not None else name).strip()
    new_variant = (body.new_variant if body.new_variant is not None
                   else variant).strip()
    if not new_name:
        raise HTTPException(400, "the new name cannot be empty")

    hit = [r for r in c.get("refs", [])
           if r.get("tag") == "outfit"
           and str((r.get("outfit") or {}).get("name") or "").strip() == name
           and str((r.get("outfit") or {}).get("variant") or "").strip() == variant]
    if not hit:
        raise HTTPException(404, "no such outfit / variant")

    # A rename onto an EXISTING (name, variant) would silently merge two
    # wardrobes into one — refuse instead, and let the caller pick another name.
    if (new_name, new_variant) != (name, variant):
        clash = any(r.get("tag") == "outfit"
                    and str((r.get("outfit") or {}).get("name") or "").strip() == new_name
                    and str((r.get("outfit") or {}).get("variant") or "").strip() == new_variant
                    for r in c.get("refs", []))
        if clash:
            raise HTTPException(409, f'"{new_name}'
                                     f'{f" / {new_variant}" if new_variant else ""}" '
                                     f"already exists — pick another name")
    for r in hit:
        o = r.setdefault("outfit", {})
        o["name"], o["variant"] = new_name, new_variant
        if body.slots is not None:
            o["slots"] = {k: str(v).strip() for k, v in body.slots.items()
                          if k in _OUTFIT_SLOTS and str(v).strip()}
        if body.extra is not None:
            o["extra"] = str(body.extra).strip()
        if body.garment_ref is not None:
            o["garment_ref"] = str(body.garment_ref).strip() or None
        r["name"] = (f"{new_name}{f' / {new_variant}' if new_variant else ''} "
                     f"— {o.get('view') or 'front'}")
    _save(slug, c)
    return {"updated": len(hit), "name": new_name, "variant": new_variant,
            "renamed": (new_name, new_variant) != (name, variant)}


class OutfitDeleteIn(BaseModel):
    """Delete a whole outfit, or one variant of it."""
    name: str
    variant: Optional[str] = None      # None = the WHOLE outfit, every variant
    view: Optional[str] = None         # optional: just one view of one variant


@router.post("/characters/{slug}/refs/{rid}/revert-upscale")
async def ref_revert_upscale(slug: str, rid: str):
    """Put a reference back to its pre-upscale original.

    v1.276.13. The companion to keeping `.orig.png`: an upscale you dislike
    should not be permanent, and comparing two engines means being able to get
    back to the starting point between runs.
    """
    c = _load(slug)
    r = _ref_by_id(c, rid)
    if not r:
        raise HTTPException(404, "reference not found")
    orig = _cdir(slug) / "refs" / f"{rid}.orig.png"
    if not orig.exists():
        raise HTTPException(409, "no pre-upscale original kept for this reference")
    src = _cdir(slug) / "refs" / f"{rid}.png"
    shutil.copy2(orig, src)
    c2 = _load(slug)
    r2 = _ref_by_id(c2, rid)
    if r2 is not None:
        for k in ("upscaled", "upscaled_at", "upscaled_size", "upscaled_engine"):
            r2.pop(k, None)
    _save(slug, c2)
    from PIL import Image as _I
    with _I.open(src) as im:
        size = list(im.size)
    return {"reverted": rid, "size": size}


class RefUpscaleIn(BaseModel):
    # v1.276.12: engine choice, matching the Character Studio's existing
    # vocabulary exactly (auto | seedvr2 | gan) rather than inventing a second
    # one. `auto` prefers SeedVR2 when a seedvr2-capable worker is online,
    # because it is the better restorer; it falls back to the GAN otherwise.
    engine: str = "auto"               # auto | seedvr2 | gan
    model_name: Optional[str] = None   # GAN model override; default from workflow
    seed: int = 42                     # SeedVR2 only
    # v1.276.9: the GAN returns 4x — 832x1216 became 3328x4864 and 5.33 MB,
    # MEASURED. A reference is uploaded to a worker on EVERY render that reads
    # it, so unbounded size costs upload time on every job and disk forever,
    # for detail Klein resamples away anyway. Capped at ~2x by default; the
    # sharpening survives, the bloat does not.
    max_side: int = 2048


def _start_ref_upscale(slug: str, rid: str, disp, engine: str = "auto",
                       max_side: int = 2048, model_name: Optional[str] = None,
                       seed: int = 42, blocking: bool = False) -> dict:
    """GAN-upscale ONE reference image, in place.

    ⚠ v1.276.29 — `blocking=True` when calling this from inside a BACKGROUND
    THREAD. `_spawn()` uses `asyncio.create_task`, which needs a running event
    loop in the CURRENT thread; from a worker thread there is none, so the
    spawn raises, the caller's `except` swallows it — and the job status has
    ALREADY been set to "running", so it hangs there forever looking like a
    slow upscale. That is exactly what the outfit face-crop step hit: workers
    idle, `in_flight 0`, status "running" for six minutes. A status set before
    the work is scheduled is a status that can lie.

    v1.276.9. The base upscaler has existed since v1.208 but only ever applied
    to the ACTIVE base — so the core set (front/back/left/right/face) could not
    be sharpened, even though those are the images every downstream render reads
    from. Lorenzo: "some may produce much greater results after upscaling ... it
    helps make a solid base."

    Same proven STUDIO_UPSCALE graph the base upscaler uses. Replaces the image
    IN PLACE and records provenance on the ref, because the point is to improve
    the reference every other job reads — a second copy alongside would just
    make `_identity_ref_paths` pick between two versions of the same view.
    """
    c = _load(slug)
    r = _ref_by_id(c, rid)
    if not r:
        raise HTTPException(404, "reference not found")
    src = _cdir(slug) / "refs" / f"{rid}.png"
    if not src.exists():
        raise HTTPException(409, "reference image missing on disk")

    # v1.276.13 — KEEP THE ORIGINAL. Upscaling in place is right (every render
    # reads this slot, a second copy would just make _identity_ref_paths choose
    # between two versions of one view) but it made the operation IRREVERSIBLE
    # and NOT IDEMPOTENT: upscale with the GAN, then try SeedVR2, and the second
    # run is upscaling an upscale. You cannot compare engines and you cannot go
    # back. So the pristine source is preserved once, on first upscale, and
    # every later upscale re-runs FROM IT rather than from the previous output.
    orig = _cdir(slug) / "refs" / f"{rid}.orig.png"
    if not orig.exists():
        try:
            shutil.copy2(src, orig)
        except Exception as e:  # noqa: BLE001
            logger.warning("klein3: could not preserve original for %s: %s", rid, e)

    st = _job(slug, "refup")
    if st.get("status") == "running":
        raise HTTPException(409, "a reference upscale is already running")

    # Engine resolution, same rule the Character Studio already uses: `auto`
    # prefers SeedVR2 when a seedvr2-capable worker is online, else the GAN.
    # ⚠ SeedVR2 lives on a node pack (ComfyUI-SeedVR2_VideoUpscaler) that is
    # NOT on every box, so an explicit request for it must fail loudly here
    # rather than silently rendering something else — "I picked seedvr2 and got
    # GAN output" is exactly the quiet mismatch this codebase keeps hunting.
    req_engine = (engine or "auto").lower()
    if req_engine not in ("auto", "seedvr2", "gan"):
        raise HTTPException(400, "engine must be auto, seedvr2 or gan")

    def _cap_online(cap: str) -> bool:
        try:
            return disp is not None and disp.select_worker(
                {cap}, set(), exclude_runpod=True) is not None
        except Exception:  # noqa: BLE001
            return False

    if req_engine == "seedvr2":
        engine = "seedvr2"
        if not _cap_online("seedvr2"):
            raise HTTPException(409, "no SeedVR2-capable worker is online — that engine "
                                     "needs the ComfyUI-SeedVR2_VideoUpscaler node pack")
    elif req_engine == "gan":
        engine = "gan"
    else:
        engine = "seedvr2" if _cap_online("seedvr2") else "gan"

    wf_name = "STUDIO_SEEDVR2.json" if engine == "seedvr2" else "STUDIO_UPSCALE.json"
    wf_path = _WORKFLOWS_DIR / wf_name
    if not wf_path.exists():
        raise HTTPException(500, f"workflow {wf_name} not found")

    model_name = model_name
    st.clear()
    st.update({"status": "running", "detail": f"upscaling {r.get('tag')} ref ({engine})",
               "error": None, "ref_id": rid, "engine": engine,
               "engine_requested": req_engine})

    def _run():
        try:
            # SeedVR2 must run on a box that HAS the node pack, so select on
            # that capability rather than the generic klein pool.
            _wk = client = None
            if engine == "seedvr2":
                try:
                    _wk = disp.select_worker({"seedvr2"}, set(), exclude_runpod=True)
                    client = disp.clients.get(_wk.url) if _wk else None
                except Exception:  # noqa: BLE001
                    _wk = client = None
            if not client:
                _wk, client = _klein_worker(disp)
            if not client:
                raise RuntimeError("no worker online")
            st["worker"] = _short_worker(getattr(_wk, "url", "worker"))
            up = f"k3_refup_{uuid4().hex[:8]}.png"
            # always from the pristine source, so engines are comparable and
            # repeated upscales never stack on each other
            client.upload_image(str(orig if orig.exists() else src), up)
            if engine == "seedvr2":
                wf = prepare_studio_seedvr2_workflow(
                    str(wf_path), image_path=up, seed=int(seed or 42),
                    resolution=max(512, min(int(max_side or 2048), 3840)))
            else:
                wf = prepare_studio_upscale_workflow(
                    str(wf_path), image_path=up,
                    model_name=model_name or _GAN_MODEL_DEFAULT)
            outputs = _run_prompt_blocking(client, wf, 600 if engine == "seedvr2" else 300)
            imgs = _images_from_outputs(outputs)
            if not imgs:
                raise RuntimeError("worker produced no image")
            pick = imgs[-1]
            data = client.download_output(pick["filename"], pick.get("subfolder", ""),
                                          pick.get("type", "output"))
            from io import BytesIO as _BIO
            from PIL import Image as _Img
            im = _Img.open(_BIO(data))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            was = im.size
            cap = max(512, min(int(max_side or 2048), 8192))
            if max(im.size) > cap:
                sc = cap / max(im.size)
                im = im.resize((max(1, round(im.width * sc)),
                                max(1, round(im.height * sc))), _Img.LANCZOS)
            src.parent.mkdir(parents=True, exist_ok=True)
            im.save(src, "PNG")                 # in place — same id, same slot
            c2 = _load(slug)
            r2 = _ref_by_id(c2, rid)
            if r2 is not None:
                r2["upscaled"] = True
                r2["upscaled_at"] = _now()
                r2["upscaled_size"] = [im.width, im.height]
                r2["upscaled_engine"] = engine
                r2["orig_size"] = list(was)
                r2["size"] = [im.width, im.height]   # what the file IS now
            _save(slug, c2)
            st.update({"status": "done",
                       "detail": f"{engine}: {was[0]}x{was[1]} -> {im.width}x{im.height}"})
        except Exception as e:  # noqa: BLE001
            logger.warning("klein3 ref upscale[%s/%s] failed: %s", slug, rid, e)
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    if blocking:
        _run()          # caller is already off the event loop
    else:
        _spawn(_run)
    return {"started": True, "ref": rid, "tag": r.get("tag"), "engine": engine}


@router.post("/characters/{slug}/refs/{rid}/upscale")
async def ref_upscale(slug: str, rid: str, body: RefUpscaleIn, request: Request):
    """Upscale ONE reference image, in place. See _start_ref_upscale."""
    return _start_ref_upscale(slug, rid, _dispatcher(request),
                              engine=body.engine, max_side=body.max_side,
                              model_name=body.model_name, seed=body.seed)


@router.post("/characters/{slug}/outfits/delete")
async def outfit_delete(slug: str, body: OutfitDeleteIn):
    """Remove outfit images and their refs. Files go too — an outfit view is a
    generated render, not source material, and leaving orphan PNGs behind is the
    kind of quiet mess that fills a disk."""
    c = _load(slug)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "outfit name required")
    want_var = None if body.variant is None else str(body.variant).strip()
    want_view = (body.view or "").strip() or None

    keep, gone = [], []
    for r in c.get("refs", []):
        if r.get("tag") != "outfit":
            keep.append(r)
            continue
        o = r.get("outfit") or {}
        if str(o.get("name") or "").strip() != name:
            keep.append(r)
            continue
        if want_var is not None and str(o.get("variant") or "").strip() != want_var:
            keep.append(r)
            continue
        if want_view and str(o.get("view") or "") != want_view:
            keep.append(r)
            continue
        gone.append(r)

    if not gone:
        raise HTTPException(404, "nothing matched that outfit / variant / view")
    for r in gone:
        for suffix in (".png", ".orig.png"):
            try:
                (_cdir(slug) / "refs" / f"{r['id']}{suffix}").unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    c["refs"] = keep
    _save(slug, c)
    return {"deleted": len(gone), "name": name,
            "variant": want_var, "view": want_view}


@router.post("/characters/{slug}/outfits")
async def outfit_generate(slug: str, body: OutfitIn, request: Request,
                          session: AsyncSession = Depends(get_session)):
    """Dress this character in a NAMED outfit, across one view or the whole set.

    Each view is dressed from that view's own reference, so the result keeps the
    pose and angle it started from and only the clothes change.
    """
    c = _load(slug)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "outfit name required")
    # v1.276.21 — "regenerate all" means THE WHOLE SET, like the base-set lane.
    # It used to mean "the views this outfit happens to have", so an outfit that
    # came out 3-of-4 could never recover the 4th no matter how often you
    # pressed it (Lorenzo: "if I regenerate all it only generates 2 of the
    # images… it should act like our base image process and ensure all views are
    # created"). An empty list is the whole set, face close-up included.
    want = [v for v in (body.views or []) if v in _OUTFIT_VIEWS] or list(_OUTFIT_VIEWS)
    variant = (body.variant or "").strip()

    if body.only_missing:
        # v1.276.16 — "regenerate the ones missing". A view counts as present
        # only if its FILE is still there, so deleting one bad view (or losing a
        # render) is exactly what makes it eligible again.
        have = {str((r.get("outfit") or {}).get("view") or "")
                for r in c.get("refs", [])
                if r.get("tag") == "outfit"
                and str((r.get("outfit") or {}).get("name") or "").strip() == name
                and str((r.get("outfit") or {}).get("variant") or "").strip() == variant
                and (_cdir(slug) / "refs" / f"{r['id']}.png").exists()}
        want = [v for v in want if v not in have]
        if not want:
            raise HTTPException(409, "nothing missing — every requested view of "
                                     "this outfit already has an image")

    sources: List[tuple] = []
    skipped: List[str] = []
    for v in want:
        # the face close-up is dressed from the character's face crop; every
        # other view from its own base image.
        rs = [r for r in _refs_by_tag(c, v)
              if (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
        if not rs:
            # ⚠ this used to be a silent drop, which is how "regenerate all"
            # could quietly produce fewer images than views and say nothing.
            skipped.append(v)
            continue
        # same preference as everywhere else: an upload beats a crop beats
        # something this app generated.
        ups = [r for r in rs if r.get("source") == "upload"]
        crops = [r for r in rs if r.get("source") == "crop"]
        pick = (ups or crops or rs)[-1]
        sources.append((v, _cdir(slug) / "refs" / f"{pick['id']}.png"))
    if not sources:
        raise HTTPException(409, "no view-tagged references to dress — generate the "
                                 "missing views first")

    st = _job(slug, "outfit")
    if st.get("status") == "running":
        raise HTTPException(409, "an outfit job is already running")
    disp = _dispatcher(request)
    seed = int(body.seed) if body.seed else random.randint(1, 2_000_000_000)
    w = max(256, min(int(body.width or 832), 2048))
    h = max(256, min(int(body.height or 1216), 2048))
    nref = max(1, min(int(body.ref_count or 3), 5))
    garment_ref = (body.garment_ref or "").strip() or None
    garment_path: Optional[str] = None
    if garment_ref:
        gp = _cdir(slug) / "refs" / f"{garment_ref}.png"
        if not gp.exists():
            raise HTTPException(404, "garment reference image not found")
        garment_path = str(gp)
    # v1.276.16: ONE PROMPT PER VIEW (see _outfit_prompt). The status keeps the
    # front-view text as a representative sample so the UI still has something
    # to show; `prompts` carries what each job actually got.
    # slots are resolved per view inside _run (they depend on what refs exist);
    # this is the representative text for the status line.
    prompts: Dict[str, str] = {}
    # the vision model is optional: no Ollama configured just means no checking,
    # never a failed render.
    ocheck_urls: List[str] = []
    ocheck_model = ""
    if body.verify:
        try:
            from backend.api.vnccs_native import _ollama_cfg
            _u, _t, _v = await _ollama_cfg(session)
            ocheck_urls, ocheck_model = list(_u or []), (_v or "")
        except Exception as e:                   # noqa: BLE001
            logger.warning("klein3 outfit verify unavailable: %s", e)
    max_tries = max(1, min(int(body.max_tries or 2), 4))
    expected_by_view = {v: _outfit_expected(body.slots or {}, body.extra or "", v)
                        for v, _ in sources}

    st.clear()
    tag_txt = f"{name}{f' / {variant}' if variant else ''}"
    st.update({"status": "running", "detail": f"{tag_txt} ×{len(sources)}",
               "error": None, "outfit": name, "variant": variant,
               "prompts": prompts, "views": [v for v, _ in sources],
               "skipped": skipped, "done": []})

    def _run():
        # Each view is dressed from ITS OWN reference (image 1), with identity
        # refs chosen RELATIVE TO THAT VIEW behind it (v1.276.16) — a frontal
        # body ref behind a left-side job was pulling the render frontal.
        used: Dict[str, List[str]] = {}
        face_slots: Dict[str, int] = {}
        # ⭐ v1.276.21 — THREE PASSES, in Lorenzo's order:
        #     1. the FRONT view          (the outfit, rendered)
        #     2. the 🙂 FACE close-up     (from that front — so it carries the
        #                                 outfit's own collar, earrings, necklace)
        #     3. everything else         (back / left / right, each given THAT
        #                                 close-up as its face reference)
        # His reasoning, and it is the right one: "use the first front generation
        # to get the face closeup, and then use that as reference for the rest,
        # so we ensure the larger face is being noted for reference with the
        # styling the clothing adds to it." A plain character face crop knows
        # nothing about this outfit; the outfit's own close-up knows both.
        # Costs no extra render — pass 3 still fans across every worker.
        pass1 = [(v, p) for v, p in sources if v == "front"]
        pass2 = [(v, p) for v, p in sources if v == "face"]

        def _face_by_crop(c_state) -> bool:
            """⭐ v1.276.29 — the close-up as a CROP of the front render.

            Lorenzo: "are we not just simply upscaling the front image, then
            cropping the face and upscaling that as well to be used as reference
            for everything else? Would make sense unless you have better logic."

            He is right, and it is better logic. Generating the close-up as its
            own Klein render meant it could disagree with the costume — and it
            did: he got a face view whose torso clothing did not match the front.
            A crop of the front render cannot disagree, because it IS the front
            render. It also costs ZERO extra Klein renders; only a cheap upscale
            to bring the cropped region back up to a useful reference size.

            Returns True when it handled the face view.
            """
            fp = None
            hits = [r for r in c_state.get("refs", [])
                    if r.get("tag") == "outfit"
                    and str((r.get("outfit") or {}).get("name") or "") == name
                    and str((r.get("outfit") or {}).get("variant") or "") == variant
                    and str((r.get("outfit") or {}).get("view") or "") == "front"
                    and (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
            if hits:
                fp = _cdir(slug) / "refs" / f"{hits[-1]['id']}.png"
            if fp is None:
                return False                      # no front yet — render it
            # ⭐ v1.276.37 — UPSCALE THE FRONT *BEFORE* CROPPING. Lorenzo asked
            # whether we were doing this; we were not. It matters, and the
            # arithmetic is the argument: a head-and-shoulders box is about 15%
            # of an 832×1216 frame, so cropping first hands the upscaler a
            # ~180×220 source and asks it to invent 16× the pixels. Upscaling
            # the front to 2048 first makes that same box ~440×540 of REAL
            # detail before anything is invented.
            # Non-destructive: the upscale goes to a temp file, never over the
            # outfit's own front render, which he has already approved.
            src_for_crop = fp
            if body.upscale_front_first:
                big = _upscale_file(fp, _cdir(slug) / "_mirror" / f"big_{Path(fp).stem}.png",
                                    disp, max_side=2048, st=st,
                                    label="upscale front before face crop")
                if big is not None:
                    src_for_crop = big
                    st.setdefault("face_source", {})["front_upscaled"] = True
            crop = _headshot_of(slug, src_for_crop)
            if not crop:
                return False                      # no face found — render it
            rid = uuid4().hex[:12]
            out = _cdir(slug) / "refs" / f"{rid}.png"
            try:
                shutil.copy2(crop, out)
            except Exception as e:                # noqa: BLE001
                logger.warning("klein3 face crop copy failed: %s", e)
                return False
            c2 = _load(slug)
            refs2 = c2.setdefault("refs", [])
            for i2, r in enumerate(list(refs2)):  # SLOT SEMANTICS, as ever
                o = r.get("outfit") or {}
                if (r.get("tag") == "outfit"
                        and str(o.get("name") or "") == name
                        and str(o.get("variant") or "") == variant
                        and str(o.get("view") or "") == "face"):
                    try:
                        (_cdir(slug) / "refs" / f"{r['id']}.png").unlink(missing_ok=True)
                    except Exception:             # noqa: BLE001
                        pass
                    refs2.pop(i2)
                    break
            refs2.append({
                "id": rid, "tag": "outfit",
                "name": f"{tag_txt} — face", "source": "crop",
                "created_at": _now(),
                "outfit": {"name": name, "variant": variant, "view": "face",
                           "slots": body.slots or {}, "extra": body.extra or "",
                           "garment_ref": garment_ref,
                           "built_from": [Path(fp).stem]},
                "size": list(_image_size(out) or []) or None})
            _save(slug, c2)
            # Bring the cropped region back up to a useful reference size, and
            # WAIT for it. ⚠ v1.276.29: kicking this off in the background meant
            # the outfit reported "done" while the face reference was still the
            # raw 182x225 crop — and everything downstream that reads it would
            # get that. A step is not finished until its output is usable.
            # GAN, not SeedVR2: this runs inside the outfit job and the GAN is
            # the fast path (300s cap vs 600s); SeedVR2 sat on a 182px crop for
            # over four minutes in testing.
            try:
                _start_ref_upscale(slug, rid, disp, engine="gan",
                                   max_side=1536, blocking=True)
                up = _job(slug, "refup")
                if up.get("error"):
                    logger.warning("klein3 face crop upscale: %s", up["error"])
                c3 = _load(slug)
                r3 = _ref_by_id(c3, rid)
                if r3 is not None:
                    st.setdefault("face_size", {})["face"] = r3.get("size")
            except Exception as e:                # noqa: BLE001
                logger.warning("klein3 face crop upscale failed: %s", e)
            st["done"] = st.get("done", []) + ["face"]
            st["detail"] = f"{tag_txt} {len(st['done'])}/{len(sources)}"
            st.setdefault("face_source", {})["face"] = "cropped from the front render"
            return True
        # ⚠ v1.276.45 — `right` is split into its OWN phase only when it is
        # actually waiting for something. With `sibling_ref` on it needs the
        # finished LEFT render, mirrored, as its garment reference — a real
        # dependency, worth a phase. With `sibling_ref` OFF it waits for
        # NOTHING, and splitting it anyway made a 5-view set four phases deep
        # with a maximum width of two on a three-box fleet: the third worker sat
        # idle through the whole set and the run took an extra render's wall
        # time for no reason.
        _right_waits = bool(body.sibling_ref)
        pass3 = [(v, p) for v, p in sources
                 if v not in ("front", "face") and (v != "right" or not _right_waits)]
        pass4 = [(v, p) for v, p in sources if v == "right"] if _right_waits else []
        st["phases"] = {"parallel": [v for v, _ in pass3],
                        "deferred": [v for v, _ in pass4],
                        "why": ("right waits for left, mirrored, as its garment "
                                "reference" if _right_waits else
                                "sibling_ref off — nothing waits, all views fan")}

        def _build(batch, c_state):
            return [_one(view, p, i, c_state)
                    for i, (view, p) in enumerate(batch)]

        def _one(view, p, i, c_state):
            refs, face_slot = _outfit_ref_paths(slug, c_state, view, str(p),
                                                limit=nref)
            if garment_path and garment_path not in refs:
                # The garment photo goes AFTER the pinned face crop. v1.276.17
                # put it at slot 2, which displaced the one reference that
                # carries identity — and identity drift in outfit renders is
                # exactly what Lorenzo reported next. Clothes are easier to
                # copy from a description than a face is, so the face wins the
                # higher slot and the garment is cited by number too.
                at = 2 if not face_slot else face_slot          # 0-based insert
                refs = refs[:at] + [garment_path] + refs[at:]
                refs = refs[:max(face_slot + 1, nref)]
            def _outfit_view(vv: str) -> Optional[str]:
                """This outfit's own render of view `vv`, if it exists yet."""
                hits = [r for r in c_state.get("refs", [])
                        if r.get("tag") == "outfit"
                        and str((r.get("outfit") or {}).get("name") or "") == name
                        and str((r.get("outfit") or {}).get("variant") or "") == variant
                        and str((r.get("outfit") or {}).get("view") or "") == vv
                        and (_cdir(slug) / "refs" / f"{r['id']}.png").exists()]
                return (str(_cdir(slug) / "refs" / f"{hits[-1]['id']}.png")
                        if hits else None)

            styled_face = False
            if view not in ("face", "back"):
                # ⭐ v1.276.21 — swap the plain character face crop for THIS
                # OUTFIT's close-up once it exists. The plain crop knows the
                # face but nothing about this outfit; the close-up knows both,
                # so the side and back views inherit the same collar, earrings
                # and necklace instead of re-inventing them at 40 pixels.
                ofc = _outfit_view("face")
                if ofc and face_slot:
                    refs[face_slot - 1] = ofc
                    styled_face = True
                elif ofc and ofc not in refs:
                    refs = refs[:1] + [ofc] + refs[1:]
                    refs = refs[:max(2, nref)]
                    face_slot, styled_face = 2, True

            # ── 🔗 v1.276.26 SIDE-TO-SIDE garment continuity (EXPERIMENT) ──
            # Lorenzo: "side views look pretty good although a little
            # inconsistent when compared as some details dont match on each
            # side." They are independent renders sharing a DESCRIPTION, not
            # pixels, so each side re-invents the trims from words.
            #
            # The fix has to respect what this lane already learned the hard
            # way: a frontal render behind a side view drags the facing
            # (v1.276.16), and so does the opposite profile (v1.276.17). But the
            # opposite profile MIRRORED faces the SAME way as the target
            # (v1.276.19) — so the other side's finished outfit render, flipped,
            # is garment evidence at the correct facing. Cited by slot number.
            sibling_slot = 0
            if body.sibling_ref and view in ("left", "right"):
                sib = _outfit_view(_OPPOSITE_VIEW[view])
                if sib:
                    try:
                        m = _flip_png(sib, _cdir(slug) / "_mirror"
                                      / f"sib_{Path(sib).stem}.png")
                        mp = str(m)
                        if mp not in refs:
                            at = face_slot if face_slot else 1
                            refs = refs[:at + 1] + [mp] + refs[at + 1:]
                            refs = refs[:max(at + 2, nref)]
                        sibling_slot = refs.index(mp) + 1
                    except Exception as e:        # noqa: BLE001
                        logger.warning("klein3 sibling garment ref failed: %s", e)

            outfit_slot = 0
            if view == "face":
                # the FRONT render of this same outfit, if one exists by now
                fp = _outfit_view("front")
                if fp:
                    # ⚠ v1.276.24 — CROP IT FIRST. The front render is a FULL
                    # BODY, and Klein reproduces a reference's framing along
                    # with its content: passing it whole turned the close-up
                    # into a bust shot showing the chest emblem, twice, on two
                    # different costumes. Lorenzo: "the face closeup seems to be
                    # a bust closeup". A head-and-shoulders crop of the SAME
                    # render carries the collar and the jewellery — which is the
                    # whole reason it is there — without dragging the framing.
                    fp = _headshot_of(slug, fp) or fp
                    if fp not in refs:
                        refs = refs[:1] + [fp] + refs[1:]
                        refs = refs[:max(2, nref)]
                        if face_slot and face_slot >= 2:
                            face_slot += 1
                    outfit_slot = refs.index(fp) + 1
            g_slot = (refs.index(garment_path) + 1) if (
                garment_path and garment_path in refs) else 0
            face_slot = face_slot if (face_slot and face_slot <= len(refs)) else 0
            prompts[view] = _outfit_prompt(
                body.slots or {}, body.extra or "", c.get("fields", {}),
                view=view, garment_slot=g_slot, face_slot=face_slot,
                outfit_slot=outfit_slot, styled_face=styled_face,
                sibling_slot=sibling_slot)
            face_slots[view] = face_slot
            used[view] = [Path(r).stem for r in refs]
            # v1.276.24 — Lorenzo: "if our costume was based on a reference or
            # multiple reference images we should show them somehow so we can
            # compare the output with the reference costume images." `refs` are
            # absolute paths, some of them derived crops under _mirror/ that are
            # not refs at all, so publish a UI-resolvable list instead: a URL
            # when the image is a real reference, a label when it is derived.
            ref_urls = []
            for rp in refs:
                stem = Path(rp).stem
                if Path(rp).parent.name == "refs" and ".orig" not in stem:
                    ref_urls.append({
                        "id": stem, "derived": False,
                        "url": f"/api/klein3/characters/{slug}/refs/{stem}/image"})
                else:
                    ref_urls.append({"id": stem, "derived": True, "url": None})
            st.setdefault("ref_images", {})[view] = ref_urls
            return {"key": view, "prompt": prompts[view], "refs": refs,
                    "w": w, "h": h, "seed": seed + i, "attempt": 1}

        st["refs_used"] = used      # answerable: what did each job actually get
        st["face_ref"] = face_slots

        def on_result(jb, data):
            rid = uuid4().hex[:12]
            _save_png_bytes(data, _cdir(slug) / "refs" / f"{rid}.png")
            # ── v1.276.22: does the render match the costume it was asked for?
            if ocheck_urls and ocheck_model:
                ok, why, findings = _outfit_verdict(
                    ocheck_urls, ocheck_model,
                    _cdir(slug) / "refs" / f"{rid}.png",
                    expected_by_view.get(jb["key"], ""))
                st.setdefault("checks", {}).setdefault(jb["key"], []).append(
                    {"attempt": jb.get("attempt", 1), "ok": ok, "why": why})
                if not ok and jb.get("attempt", 1) < max_tries:
                    fix = _correction_clause(findings)
                    st["detail"] = (f"{tag_txt} · re-rendering {jb['key']} "
                                    f"({jb.get('attempt', 1) + 1}/{max_tries}): {why}")
                    try:
                        (_cdir(slug) / "refs" / f"{rid}.png").unlink(missing_ok=True)
                    except Exception:            # noqa: BLE001
                        pass
                    nxt = dict(jb)
                    nxt["attempt"] = jb.get("attempt", 1) + 1
                    # ⚠ the correction goes FIRST, not appended. Measured on a
                    # Supergirl costume: the word "supergirl" carries a strong
                    # prior toward Clark-Kent glasses, and a correction tacked
                    # on the end of a long prompt lost to it twice. Leading
                    # position is the only emphasis lever available at cfg=1
                    # with no negative prompt and no weighting syntax.
                    nxt["prompt"] = fix.strip() + " " + jb["prompt"]
                    nxt["seed"] = jb["seed"] + 5099 * nxt["attempt"]
                    return nxt
                if not ok:
                    # keep it, but say so — an outfit view is decorative enough
                    # that a flawed one beats a hole, unlike a base view.
                    st.setdefault("flagged", []).append(
                        {"view": jb["key"], "why": why + _name_hint(
                            body.slots or {}, findings)})
            c2 = _load(slug)
            rec = {
                "id": rid, "tag": "outfit",
                "name": f"{tag_txt} — {jb['key']}",
                "source": "generated", "created_at": _now(),
                "outfit": {"name": name, "variant": variant, "view": jb["key"],
                           "slots": body.slots or {}, "extra": body.extra or "",
                           "garment_ref": garment_ref,
                           # what this image was actually built from, so the
                           # wardrobe can show it next to the result forever —
                           # not just while the job status is still in memory
                           "built_from": [r["id"] for r in
                                          (st.get("ref_images", {}).get(jb["key"]) or [])
                                          if not r["derived"]]},
            }
            # v1.276.9 SLOT SEMANTICS, same as the Strip SET: an outfit holds ONE
            # image per (name, variant, view). Re-running REPLACES that slot in
            # place instead of growing the list, so "regenerate after changing
            # the base images" is the same button as "generate" and the wardrobe
            # never silently accumulates six versions of the same jacket.
            refs = c2.setdefault("refs", [])
            old_idx = None
            for i, r in enumerate(refs):
                if r.get("tag") != "outfit":
                    continue
                o = r.get("outfit") or {}
                if (str(o.get("name") or "") == name
                        and str(o.get("variant") or "") == variant
                        and str(o.get("view") or "") == jb["key"]):
                    old_idx = i
                    break
            if old_idx is None:
                refs.append(rec)
            else:
                stale = refs[old_idx]
                refs[old_idx] = rec
                try:
                    (_cdir(slug) / "refs" / f"{stale['id']}.png").unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
            _save(slug, c2)
            st["done"] = st.get("done", []) + [jb["key"]]
            st["detail"] = f"{tag_txt} {len(st['done'])}/{len(sources)}"

        try:
            for label, batch in (("front", pass1), ("face", pass2),
                                 ("views", pass3), ("right", pass4)):
                if not batch:
                    continue
                if label == "face" and body.face_from_front and _face_by_crop(_load(slug)):
                    continue                      # handled without a render
                # reload between passes: pass 2 needs the front render pass 1
                # just wrote, pass 3 needs the close-up pass 2 just wrote.
                built = _build(batch, _load(slug))
                st["refs_used"] = used
                st["prompt"] = prompts.get(batch[0][0], "")
                st["phase"] = label
                _parallel_klein_edits(disp, built, on_result, st)
            errs = [f"{k}: {t.get('error')}" for k, t in st.get("tasks", {}).items()
                    if t.get("status") == "error"]
            errs += [f"{f['view']}: {f['why']}" for f in st.get("flagged", [])]
            if skipped:
                errs.append("no base view to dress for: " + ", ".join(skipped)
                            + " — generate those views first")
            st["error"] = "; ".join(errs) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
            # 🪪 v1.277.12 — a finished outfit AUTO-BUILDS its per-outfit
            # character sheet (single costume, 2048px — the MiniMax identity
            # anchor), so it is immediately usable as a video reference.
            # Direct call (pure PIL, we are already in a worker thread);
            # best-effort — the outfit succeeded either way.
            if st["status"] in ("done", "done_with_errors"):
                try:
                    from backend.api.charsheet import _compose as _sheet_compose
                    c3 = _load(slug)
                    sheet_name = f"{c3.get('name') or slug} — {name}" \
                                 + (f" ({variant})" if variant else "")
                    meta = _sheet_compose(slug, sheet_name, "outfit", False,
                                          2048, {"name": name,
                                                 "variant": variant})
                    st["outfit_sheet"] = meta.get("file")
                except Exception as e:  # noqa: BLE001
                    logger.warning("klein3 %s: auto outfit sheet failed: %s",
                                   slug, e)
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "outfit": name, "variant": variant,
            "views": [v for v, _ in sources], "skipped": skipped}


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
    #: ⚠ v1.276.20 — this used to say "default from workflow", and the workflow's
    #: baked-in default is the ANIME model (4x_APISR_GRL_GAN_generator.pth). So
    #: while v1.276.14 fixed the face-crop and reference-upscale paths, the
    #: ACTIVE BASE upscale quietly kept posterising faces and drawing line-art
    #: hair. Same fix, same reason: None now means _GAN_MODEL_DEFAULT.
    model_name: Optional[str] = None   # None -> _GAN_MODEL_DEFAULT (photoreal)


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
    model_name = body.model_name or _GAN_MODEL_DEFAULT
    st.clear()
    st.update({"status": "running", "detail": "upscale", "error": None,
               "model": model_name})

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
