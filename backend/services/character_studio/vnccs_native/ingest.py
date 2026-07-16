"""Ingest VNCCS generation outputs into OUR system (asset store + Studio catalog).

After a VNCCS meganode graph runs on the host, its tapped SaveImage outputs live
in the worker's output folder.  This module downloads them, files them into the
hidden Character-Studio project's asset store (the same convention the rest of
Studio uses), and records the result on a ``StudioCharacter`` so the character is
cataloged and project-linkable in our app — not just a folder on the worker.

The VNCCS link is stored under ``StudioCharacter.manifest["vnccs"]`` (reusing the
existing JSON column — no schema migration):

    manifest["vnccs"] = {
      "host": ..., "prompt_id": ..., "ref": <vnccs character name>,
      "step": "creator"|"cloner"|"clothes"|"emotions",
      "outputs": { "{step}/{label}": [asset_id, ...] },  # e.g. "creator/sheet"
      "hero_asset_id": <id of a representative image>,
      "updated_at": iso,
    }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import select

from backend.config import settings as cfg
from backend.database.models import (
    Asset, AssetType, Project, ProjectMode, StudioCharacter,
)
from .client import VNCCSClient, VNCCSError


def _png_bytes_complete(data: bytes) -> bool:
    """True if ``data`` is a fully-decodable image.  Guards against truncated
    /view downloads that otherwise get written to disk as corrupt, half-black
    sprites (the "incredibly dark / graphical issues" pose-sprite failure)."""
    if not data:
        return False
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as _im:
            _im.load()            # forces a full decode; raises on truncation
        return True
    except Exception:
        return False


def _has_transparency(path) -> bool:
    """True if the PNG already carries a real alpha channel with transparent
    pixels -- i.e. the worker (VNCCS RMBG2) already removed the background, so
    the app-side cutout should be skipped."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.mode not in ("RGBA", "LA") and not (im.mode == "P" and "transparency" in im.info):
                return False
            a = im.convert("RGBA").getchannel("A")
            lo, _hi = a.getextrema()
            return lo < 250
    except Exception:
        return False


def _defringe_sprite(path) -> bool:
    """Kill the dark/green matte halo on a cut-out pose sprite's edge so its
    silhouette reads as cleanly as the base.

    Pose sprites are rendered on a flat GREEN field then matted to transparency;
    rembg/RMBG2 leave an antialiased rim that still carries green spill (reads as
    a dark/green outline) -- the base never shows this because its preview stays
    on a clean field.  We (1) despill: on the semi-transparent rim clamp the green
    channel down to max(red, blue) wherever green dominates (pure green-spill
    removal; never touches red/blue subject edges), and (2) erode the alpha by 1px
    (min-filter) so the outermost fringe ring is dropped -- imperceptible on the
    figure.  Best-effort: leaves the file untouched on any failure."""
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:  # noqa: BLE001
        return False
    try:
        with Image.open(path) as _im:
            im = _im.convert("RGBA")
        arr = np.asarray(im).astype(np.float32)
        if arr.ndim != 3 or arr.shape[-1] < 4:
            return False
        a = arr[..., 3]
        edge = (a > 8) & (a < 248)                 # antialiased / semi-transparent rim
        if bool(edge.any()):
            rgb = arr[..., :3]
            other_max = np.maximum(rgb[..., 0], rgb[..., 2])   # max(red, blue)
            spill = edge & (rgb[..., 1] > other_max)
            rgb[..., 1] = np.where(spill, other_max, rgb[..., 1])
            arr[..., :3] = rgb
        out = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGBA")
        alpha = out.getchannel("A").filter(ImageFilter.MinFilter(3))  # erode 1px
        out.putalpha(alpha)
        out.save(path, "PNG")
        return True
    except Exception:  # noqa: BLE001
        return False


logger = logging.getLogger(__name__)

# Which tap label is the "hero" (thumbnail) per step, best first.
_HERO_PREFERENCE = ("upscaled", "sheet", "original_upscaled", "sprites", "faces",
                    "pose_generation", "original_sprites")

# Parallel fan-out chunks ingest concurrently; serialize the manifest
# read-modify-write so chunks don't drop each other's asset ids.
_MANIFEST_LOCK = asyncio.Lock()


async def _studio_project(session) -> Project:
    rows = (await session.execute(select(Project))).scalars().all()
    for p in rows:
        if (p.settings or {}).get("studio_system"):
            return p
    proj = Project(name="_Character Studio (system)", mode=ProjectMode.MUSIC_VIDEO,
                   settings={"studio_system": True, "characters": []})
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return proj


async def _find_or_create_character(session, name: str,
                                    story_id: Optional[UUID]) -> StudioCharacter:
    q = select(StudioCharacter).where(StudioCharacter.name == name)
    if story_id is not None:
        q = q.where(StudioCharacter.story_id == story_id)
    existing = (await session.execute(q)).scalars().first()
    if existing:
        return existing
    trigger = "".join(ch for ch in name.lower() if ch.isalnum())[:12] or "char"
    c = StudioCharacter(
        name=name, story_id=story_id, kind="character", trigger_word=trigger,
        class_word="person", description="",
        character_info={"vnccs_native": True},
        manifest={"shots": {}},
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


async def _create_asset_row(session, proj: Project, rel_path: Path,
                            asset_type: AssetType, meta: Optional[dict]) -> Asset:
    from backend.utils.file_utils import sha256_file
    abs_path = Path(cfg.project_dir) / str(proj.id) / rel_path
    try:
        sha = sha256_file(abs_path)
    except Exception:
        sha = ""
    asset = Asset(
        project_id=proj.id, filename=abs_path.name, rel_path=str(rel_path),
        asset_type=asset_type, sha256=sha or "",
        file_size=abs_path.stat().st_size if abs_path.exists() else 0,
        meta=meta or {},
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


def _history_outputs(client: VNCCSClient, prompt_id: str) -> Optional[Dict[str, Any]]:
    hist = client.get_history(prompt_id, timeout=30)
    entry = hist.get(prompt_id) if isinstance(hist, dict) else None
    if not entry:
        return None
    return entry.get("outputs") or None


async def save_base_preview(session, *, character_name: str,
                            image_b64: Optional[str] = None,
                            views: Optional[List[Dict[str, Any]]] = None,
                            story_id: Optional[UUID] = None,
                            variant: Optional[str] = None,
                            gen_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Persist a "Generate Character" preview as a BASE-IMAGE VERSION.

    A base version is now a SET of views (front/left/right/back) when ``views``
    is supplied ([{"view", "image_b64"}, ...]); the FRONT view is the PRIMARY
    (``asset_id``/``url``) so lock-base and older single-image consumers keep
    working, and every view is recorded under ``entry["views"]``.  Passing just
    ``image_b64`` stores a single-view (front) version, as before.

    Each version appends to ``manifest["vnccs"]["base_versions"]`` and becomes
    the ACTIVE base (latest = default). Pose runs ingested while a version is
    active are tagged with it, so tweaking the base and re-running keeps every
    iteration linked.
    """
    import base64 as _b64
    from uuid import uuid4
    proj = await _studio_project(session)
    char = await _find_or_create_character(session, character_name, story_id)

    items: List[Any] = []
    if views:
        for vw in views:
            b64 = (vw or {}).get("image_b64")
            if b64:
                items.append((str((vw or {}).get("view") or "front"), _b64.b64decode(b64)))
    elif image_b64:
        items.append(("front", _b64.b64decode(image_b64)))
    if not items:
        raise VNCCSError("save_base_preview: no image data provided")

    ver_id = uuid4().hex[:12]
    view_entries: List[Dict[str, Any]] = []
    primary: Optional[Dict[str, Any]] = None
    for vlabel, data in items:
        rel = Path("assets") / "vnccs" / _safe(character_name) / "base" / f"base_{ver_id}_{vlabel}.png"
        abs_dest = Path(cfg.project_dir) / str(proj.id) / rel
        abs_dest.parent.mkdir(parents=True, exist_ok=True)
        abs_dest.write_bytes(data)
        asset = await _create_asset_row(
            session, proj, rel, AssetType.CHARACTER,
            meta={"vnccs": {"label": "base", "character": character_name,
                            "base_version": ver_id, "view": vlabel}})
        url = f"/api/files/{proj.id}/" + str(rel).replace("\\", "/")
        ventry = {"view": vlabel, "asset_id": str(asset.id), "url": url}
        view_entries.append(ventry)
        if primary is None or vlabel == "front":
            primary = ventry

    manifest = dict(char.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    versions = list(v.get("base_versions") or [])
    entry = {"id": ver_id, "asset_id": primary["asset_id"], "url": primary["url"],
             "views": view_entries, "created_at": datetime.utcnow().isoformat(),
             "gen_meta": dict(gen_meta or {})}
    versions.append(entry)
    v["base_versions"] = versions
    v["active_base"] = ver_id                # latest version = default active
    if variant in ("native", "klein"):
        v["variant"] = variant               # which studio mode made this char
    v.setdefault("ref", character_name)
    manifest["vnccs"] = v
    char.manifest = manifest
    await session.commit()
    return {"character_id": str(char.id), "version": entry,
            "count": len(versions), "active": ver_id}


async def save_pose_upscale(session, *, character_name: str, label: str,
                            image_bytes: bytes, src_asset_id: str,
                            src_meta: Optional[Dict[str, Any]] = None,
                            upscale_method: Optional[str] = None,
                            story_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Persist an UPSCALED copy of a pose sprite as a NEW asset that PRESERVES the
    original.  The new asset carries the original's vnccs tags (step/label/
    pose_name/base_version/costume) plus ``upscaled: True`` and
    ``upscale_source`` = the ORIGINAL asset id, so re-upscaling always resolves
    back to the original (no upscale-on-upscale stacking).  Any earlier upscale of
    the SAME original is replaced (removed from ``outputs`` and its file/row
    deleted best-effort), so the gallery keeps at most one upscale per pose.

    Returns ``{asset_id, url, label, replaced}``.  ``label`` is the same
    step-namespaced key the pose lives under (e.g. ``creator/sprites``) so the
    upscale shows in the same gallery row.
    """
    from uuid import uuid4
    proj = await _studio_project(session)
    char = await _find_or_create_character(session, character_name, story_id)
    sm = dict(src_meta or {})
    uid = uuid4().hex[:12]
    rel = (Path("assets") / "vnccs" / _safe(character_name) / "upscaled"
           / f"up_{uid}.png")
    abs_dest = Path(cfg.project_dir) / str(proj.id) / rel
    abs_dest.parent.mkdir(parents=True, exist_ok=True)
    abs_dest.write_bytes(image_bytes)
    meta_v = {k: sm.get(k) for k in ("step", "label", "pose_name",
                                     "base_version", "costume")
              if sm.get(k) is not None}
    meta_v.update({"character": character_name, "upscaled": True,
                   "upscale_source": str(src_asset_id),
                   "upscale_method": upscale_method or "?"})
    asset = await _create_asset_row(
        session, proj, rel, AssetType.CHARACTER, meta={"vnccs": meta_v})
    url = f"/api/files/{proj.id}/" + str(rel).replace("\\", "/")

    manifest = dict(char.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    outputs = dict(v.get("outputs") or {})
    lst = list(outputs.get(label) or [])
    # replace any prior upscale of the SAME original (keep one upscale per pose)
    replaced = 0
    stale: List[str] = []
    for aid in lst:
        try:
            a = await session.get(Asset, UUID(str(aid)))
        except Exception:  # noqa: BLE001
            a = None
        amv = ((a.meta or {}).get("vnccs") or {}) if a is not None else {}
        if amv.get("upscaled") and str(amv.get("upscale_source")) == str(src_asset_id):
            stale.append(str(aid))
    for aid in stale:
        if aid in lst:
            lst.remove(aid)
        try:
            a = await session.get(Asset, UUID(str(aid)))
            if a is not None:
                ap = Path(cfg.project_dir) / str(a.project_id) / str(a.rel_path).replace("\\", "/")
                try:
                    if ap.exists():
                        ap.unlink()
                except Exception:  # noqa: BLE001
                    pass
                await session.delete(a)
                replaced += 1
        except Exception:  # noqa: BLE001
            pass
    lst.append(str(asset.id))
    outputs[label] = lst
    v["outputs"] = outputs
    v.setdefault("ref", character_name)
    manifest["vnccs"] = v
    char.manifest = manifest
    await session.commit()
    return {"asset_id": str(asset.id), "url": url, "label": label,
            "replaced": replaced}


async def save_costume_preview(session, *, character_name: str, costume: str,
                               image_b64: str, costume_info: Optional[Dict[str, Any]] = None,
                               story_id: Optional[UUID] = None,
                               gen_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Persist a costume preview as a COSTUME VERSION (per character+costume).

    Each version snapshots the costume_info prompts that produced it, so
    selecting an older image restores the exact prompt set for tweaking.
    Newest version becomes the active default for that costume."""
    import base64 as _b64
    from uuid import uuid4
    proj = await _studio_project(session)
    char = await _find_or_create_character(session, character_name, story_id)
    data = _b64.b64decode(image_b64)
    ver_id = uuid4().hex[:12]
    rel = (Path("assets") / "vnccs" / _safe(character_name) /
           f"costume_{_safe(costume)}" / f"preview_{ver_id}.png")
    abs_dest = Path(cfg.project_dir) / str(proj.id) / rel
    abs_dest.parent.mkdir(parents=True, exist_ok=True)
    abs_dest.write_bytes(data)
    asset = await _create_asset_row(
        session, proj, rel, AssetType.CHARACTER,
        meta={"vnccs": {"label": "costume_preview", "character": character_name,
                        "costume": costume, "costume_version": ver_id}})
    url = f"/api/files/{proj.id}/" + str(rel).replace("\\", "/")
    manifest = dict(char.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    costumes = dict(v.get("costumes") or {})
    entry_map = dict(costumes.get(costume) or {})
    versions = list(entry_map.get("versions") or [])
    entry = {"id": ver_id, "asset_id": str(asset.id), "url": url,
             "created_at": datetime.utcnow().isoformat(),
             "costume_info": dict(costume_info or {}),
             "gen_meta": dict(gen_meta or {})}
    versions.append(entry)
    entry_map["versions"] = versions
    entry_map["active"] = ver_id
    entry_map["costume_info"] = dict(costume_info or {})  # costume-level working prompts
    costumes[costume] = entry_map
    v["costumes"] = costumes
    v.setdefault("ref", character_name)
    manifest["vnccs"] = v
    char.manifest = manifest
    await session.commit()
    return {"character_id": str(char.id), "costume": costume, "version": entry,
            "count": len(versions), "active": ver_id}


async def save_costume_info(session, *, character_name: str, costume: str,
                            costume_info: Optional[Dict[str, Any]] = None,
                            story_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Persist an outfit's WORKING prompt set on the character manifest without
    generating anything (Clothes tab 💾 / auto-save after runs).  Creates the
    costume entry if it doesn't exist yet."""
    char = await _find_or_create_character(session, character_name, story_id)
    manifest = dict(char.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    costumes = dict(v.get("costumes") or {})
    entry_map = dict(costumes.get(costume) or {})
    entry_map["costume_info"] = dict(costume_info or {})
    costumes[costume] = entry_map
    v["costumes"] = costumes
    v.setdefault("ref", character_name)
    manifest["vnccs"] = v
    char.manifest = manifest
    session.add(char)
    await session.commit()
    return {"character_id": str(char.id), "costume": costume, "ok": True}


async def ingest_result(
    session,
    *,
    host: str,
    prompt_id: str,
    character_name: str,
    step: str,
    tap_map: Dict[str, str],
    story_id: Optional[UUID] = None,
    asset_type: AssetType = AssetType.CHARACTER,
    base_version: Optional[str] = None,
    costume: Optional[str] = None,
    emotions: Optional[List[str]] = None,
    costumes: Optional[List[str]] = None,
    seed: Optional[int] = None,
    pose_names: Optional[List[str]] = None,
    pose_set_full: Optional[List[Dict[str, Any]]] = None,
    postprocess: Optional[str] = None,
    chunk_pose_names: Optional[List[str]] = None,
    engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Download tapped outputs for ``prompt_id`` and catalog them on a character.

    Returns a summary dict {character_id, ref, outputs:{label:[asset_id]}, hero_asset_id}.
    Raises ``VNCCSError`` if the job isn't finished yet (no outputs).
    """
    client = VNCCSClient(host, timeout=60)
    outputs = await asyncio.to_thread(_history_outputs, client, prompt_id)
    if not outputs:
        raise VNCCSError(f"prompt {prompt_id} has no outputs yet (still running or failed)")

    # invert tap_map (label -> save_node_id)  =>  node_id -> label
    node_label = {sid: label for label, sid in (tap_map or {}).items()}

    proj = await _studio_project(session)
    char = await _find_or_create_character(session, character_name, story_id)
    _v = (char.manifest or {}).get("vnccs") or {}
    if base_version is None:
        # link this run to the character's currently-active base image version
        base_version = _v.get("active_base")
    costume_version = None
    if costume:
        costume_version = ((_v.get("costumes") or {}).get(costume) or {}).get("active")

    base_rel = Path("assets") / "vnccs" / _safe(character_name)
    ingested: Dict[str, List[str]] = {}
    asset_urls: Dict[str, str] = {}
    hero_asset_id: Optional[str] = None

    for node_id, out in outputs.items():
        label = node_label.get(str(node_id)) or node_label.get(node_id)
        images = out.get("images", []) or []
        if not label or not images:
            continue
        asset_ids: List[str] = []
        for i, img in enumerate(images):
            fn = img.get("filename")
            if not fn:
                continue
            data = None
            for _attempt in range(3):
                try:
                    got = await asyncio.to_thread(
                        client.view_image, fn, img.get("subfolder", ""),
                        img.get("type", "output"), 60)
                except VNCCSError as e:
                    logger.warning("ingest: download failed for %s/%s (attempt %d/3): %s",
                                   label, fn, _attempt + 1, e)
                    await asyncio.sleep(0.4)
                    continue
                if got and _png_bytes_complete(got):
                    data = got
                    break
                logger.warning("ingest: truncated/undecodable image %s/%s "
                               "(attempt %d/3, %d bytes) -- retrying",
                               label, fn, _attempt + 1, len(got or b""))
                await asyncio.sleep(0.4)
            if not data:
                logger.error("ingest: giving up on %s/%s after 3 attempts "
                             "(corrupt/truncated download)", label, fn)
                continue
            # Namespace the stored file by prompt_id.  Parallel fan-out chunks
            # run on workers whose ComfyUI SaveImage counters are INDEPENDENT,
            # so different chunks routinely emit identical filenames (e.g.
            # klein_sprites_00001_.png on every fresh worker).  Writing them to
            # the same label folder made later chunks OVERWRITE earlier ones on
            # our disk -> "N rows of the same M images".  prompt_id is unique
            # per chunk/job, so this guarantees distinct on-disk paths.
            pid_safe = "".join(ch for ch in str(prompt_id) if ch.isalnum())[:16] or "job"
            rel = base_rel / label / f"{pid_safe}_{fn}"
            asset_url = f"/api/files/{proj.id}/" + str(rel).replace("\\", "/")
            abs_dest = Path(cfg.project_dir) / str(proj.id) / rel
            abs_dest.parent.mkdir(parents=True, exist_ok=True)
            abs_dest.write_bytes(data)
            # Klein runs get their backgrounds prompted, not node-removed —
            # app-side chroma/rembg cutout makes the sprites drop-in equals of
            # the Qwen pipeline's BG-removed finals.
            if postprocess == "chroma" and ("sprites" in label or label.endswith("sheet")):
                try:
                    if _has_transparency(abs_dest):
                        logger.info("ingest: %s already background-removed on the worker "
                                    "(VNCCS RMBG2) -- skipping app-side cutout", fn)
                    else:
                        from backend.services.character_studio.cutout import (
                            chroma_key_cutout, cutout_cpu, rembg_cutout)
                        # rembg (subject segmentation) is background-INDEPENDENT and
                        # the most robust for full-body sprites, where a frame-filling
                        # figure contaminates the chroma key's border-ring bg sample
                        # (-> semi-transparent/dark character).  Prefer it when
                        # installed; else chroma-key the solid backdrop; else crude.
                        ok, note = await asyncio.to_thread(rembg_cutout, abs_dest, abs_dest)
                        if not ok:
                            ok, note = await asyncio.to_thread(chroma_key_cutout, abs_dest, abs_dest)
                        if not ok:
                            logger.warning("ingest: chroma-key failed (%s) for %s; "
                                           "falling back to cpu", note, fn)
                            ok, note = await asyncio.to_thread(cutout_cpu, abs_dest, abs_dest)
                        if not ok:
                            logger.warning("ingest: cutout failed for %s: %s", fn, note)
                        elif note:
                            logger.info("ingest: cutout note for %s: %s", fn, note)
                    # despill + 1px alpha erode so the sprite edge loses the dark/green
                    # matte halo (rembg/RMBG2 leave one; the base never shows it) -- runs
                    # on BOTH the worker-RMBG2 path and the app-side cutout path
                    await asyncio.to_thread(_defringe_sprite, abs_dest)
                except Exception:  # noqa: BLE001 — cutout is best-effort
                    logger.exception("ingest: chroma cutout crashed for %s", fn)
            # when this chunk's pose names align 1:1 with the tap's images,
            # each image is tagged with ITS pose — the replacement key
            pose_name = (chunk_pose_names[i]
                         if chunk_pose_names and len(images) == len(chunk_pose_names)
                         else None)
            asset = await _create_asset_row(
                session, proj, rel, asset_type,
                meta={"vnccs": {"label": label, "character": character_name,
                                "step": step, "prompt_id": prompt_id, "index": i,
                                "base_version": base_version,
                                "costume": costume, "costume_version": costume_version,
                                "seed": seed, "pose_name": pose_name,
                                "emotions": emotions if step == "emotions" else None,
                                "costume_sets": costumes if step == "emotions" else None}},
            )
            asset_ids.append(str(asset.id))
            asset_urls[str(asset.id)] = asset_url
        if asset_ids:
            ingested[label] = asset_ids

    # pick a hero image for the thumbnail
    for pref in _HERO_PREFERENCE:
        if ingested.get(pref):
            hero_asset_id = ingested[pref][0]
            break
    if hero_asset_id is None:
        for ids in ingested.values():
            if ids:
                hero_asset_id = ids[0]
                break

    # record on the character manifest (reassign whole dict so SQLAlchemy tracks
    # it).  Parallel chunks ingest concurrently — take the lock and re-read the
    # row so each chunk merges into the latest manifest instead of clobbering.
    async with _MANIFEST_LOCK:
        await session.refresh(char)
        manifest = dict(char.manifest or {})
        vnccs = dict(manifest.get("vnccs") or {})
        vnccs.update({
            "host": host, "prompt_id": prompt_id, "ref": character_name, "step": step,
            "updated_at": datetime.utcnow().isoformat(),
        })
        # clone precedence: a cloner run marks the character as clone-made;
        # a creator run only sets 'new' if it was never cloned
        if step == "cloner":
            vnccs["create_mode"] = "clone"
        elif step == "creator":
            vnccs.setdefault("create_mode", "new")
        # emotion runs are recorded with their full recipe so any set can be
        # reviewed and REGENERATED later (emotions x costumes x seed)
        if step == "emotions" and (emotions or costumes):
            runs = list(vnccs.get("emotion_runs") or [])
            runs.append({"emotions": list(emotions or []), "costumes": list(costumes or []),
                         "seed": seed, "prompt_id": prompt_id, "host": host,
                         "at": datetime.utcnow().isoformat()})
            vnccs["emotion_runs"] = runs[-30:]
        # pose runs recorded the same way (creator/cloner/clothes). Every
        # parallel chunk carries the FULL run recipe, so dedupe on
        # step+seed+costume+names — one record per logical run.
        if step in ("creator", "cloner", "clothes") and pose_names:
            pruns = list(vnccs.get("pose_runs") or [])
            names = [str(x) for x in pose_names]
            dup = any(r.get("step") == step and r.get("seed") == seed
                      and (r.get("costume") or None) == (costume or None)
                      and r.get("pose_names") == names
                      for r in pruns[-6:])
            if not dup:
                pruns.append({"step": step, "costume": costume,
                              "pose_names": names,
                              "pose_set": list(pose_set_full or []),
                              "seed": seed, "prompt_id": prompt_id, "host": host,
                              "at": datetime.utcnow().isoformat()})
                vnccs["pose_runs"] = pruns[-12:]
        # namespace outputs by step so re-running clothes/emotions doesn't clobber
        # the creator's same-named labels, and MERGE with what's already cataloged
        # so each new batch/chunk ADDS poses instead of replacing the earlier list
        # (the user builds a character a few poses at a time).
        all_outputs = dict(vnccs.get("outputs") or {})
        for _label, _ids in ingested.items():
            key = f"{step}/{_label}"
            prev = [str(x) for x in (all_outputs.get(key) or [])]
            all_outputs[key] = prev + [i for i in _ids if i not in prev]
        # REGENERATION REPLACES: when this chunk carries pose names, older
        # images of the SAME pose (same label, costume and base version) are
        # deleted — rerunning a pose (individually, or with new options like
        # upscaling) swaps the images instead of piling up near-duplicates.
        # Assets from before this feature have no pose_name tag and are kept.
        if chunk_pose_names:
            from pathlib import Path as _Path
            names = {str(x) for x in chunk_pose_names}
            replaced = 0
            for _label, _ids in ingested.items():
                key = f"{step}/{_label}"
                keep: List[str] = []
                for aid in (all_outputs.get(key) or []):
                    if aid in _ids:
                        keep.append(aid)
                        continue
                    try:
                        old = await session.get(Asset, UUID(str(aid)))
                    except Exception:  # noqa: BLE001
                        old = None
                    mv = ((old.meta or {}).get("vnccs") or {}) if old is not None else {}
                    if (old is not None and str(mv.get("pose_name")) in names
                            and (mv.get("costume") or None) == (costume or None)
                            and (mv.get("base_version") or None) == (base_version or None)):
                        rel_o = str(old.rel_path).replace("\\", "/")
                        pid_o = str(old.project_id)
                        p_o = (Path(cfg.project_dir) / rel_o if rel_o.startswith(pid_o + "/")
                               else Path(cfg.project_dir) / pid_o / rel_o)
                        try:
                            if p_o.exists():
                                p_o.unlink()
                        except Exception:  # noqa: BLE001
                            logger.warning("ingest replace: could not remove %s", p_o)
                        await session.delete(old)
                        replaced += 1
                        continue
                    keep.append(aid)
                all_outputs[key] = keep
            if replaced:
                logger.info("ingest: replaced %d older image(s) of regenerated poses (%s)",
                            replaced, character_name)
        vnccs["outputs"] = all_outputs
        # which studio mode generated this run — Klein wins once set (a char
        # made in Klein mode stays labeled Klein even if a helper native run
        # touches it later); pure-native chars default to 'native'.
        if (engine or "").lower() == "klein":
            vnccs["variant"] = "klein"
        else:
            vnccs.setdefault("variant", "native")
        if hero_asset_id and not vnccs.get("hero_locked"):
            vnccs["hero_asset_id"] = hero_asset_id
            if asset_urls.get(hero_asset_id):
                vnccs["hero_url"] = asset_urls[hero_asset_id]
        elif hero_asset_id:
            hero_asset_id = vnccs.get("hero_asset_id") or hero_asset_id
        manifest["vnccs"] = vnccs
        char.manifest = manifest

        info = dict(char.character_info or {})
        info["vnccs_native"] = True
        char.character_info = info

        session.add(char)
        await session.commit()
        await session.refresh(char)

    return {
        "character_id": str(char.id),
        "ref": character_name,
        "step": step,
        "outputs": ingested,
        "hero_asset_id": hero_asset_id,
        "project_id": str(proj.id),
    }


def _safe(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in " _-") else "_" for ch in (name or "char")).strip() or "char"
