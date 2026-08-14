"""Tools section API — Pose Organizer / Pose Library + Expression Organizer /
Expression Library.

Design: poses are stored CANONICALLY as VNCCS 18-joint keypoints (re-renderable
to any control format). The Organizer scans a folder or zip, classifies each
file (heuristics first), converts keypoints, auto-tags from geometry, dedupes,
and produces review candidates; the user commits selected ones to the library.
Expressions store a name + natural-language prompt (+ optional reference image),
the way the emotion engines consume them.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.database.database import async_session, get_session
from backend.database.models import ExpressionLibrary, LibraryScan, PoseLibrary
from backend.services.character_studio import pose_renderer as _pr
from backend.services.character_studio.service import load_catalog
from backend.services.tools import pose_classify as _pc
from backend.services.comfyui.workflow import (
    prepare_zimage_workflow, prepare_krea2_workflow,
    prepare_anima_workflow, prepare_klein_workflow,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tools", tags=["tools"])

_LIB_ROOT = Path(cfg.project_dir) / "_libraries"
_POSE_THUMBS = _LIB_ROOT / "pose" / "thumbs"
_EXPR_THUMBS = _LIB_ROOT / "expression" / "thumbs"
_SCAN_DIR = _LIB_ROOT / "_scans"
_EXPR_REFS = _LIB_ROOT / "expression" / "refs"
_POSE_SOURCES = _LIB_ROOT / "pose" / "sources"
_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows"
MAX_SCAN = 5000


def _ensure_dirs() -> None:
    for d in (_POSE_THUMBS, _EXPR_THUMBS, _SCAN_DIR, _EXPR_REFS, _POSE_SOURCES):
        d.mkdir(parents=True, exist_ok=True)


# ── Pose Organizer ──────────────────────────────────────────────────────────
def _iter_zip_files(raw: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        return
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        yield zi.filename, (lambda z=zi: zf.read(z))


def _iter_folder_files(folder: Path):
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            yield str(p.relative_to(folder)), (lambda pp=p: pp.read_bytes())


def _pair_key(name: str) -> str:
    return Path(name).stem.lower()


async def _run_pose_scan(scan_id: UUID, files: list[tuple[str, bytes]], run_vision: bool) -> None:
    """Background: classify + convert + tag + dedupe every file into candidates."""
    _ensure_dirs()
    scan_tmp = _SCAN_DIR / str(scan_id)
    scan_tmp.mkdir(parents=True, exist_ok=True)

    # Group by filename stem so a `pose_001.json` + `pose_001.png` pair together.
    by_stem: dict[str, dict] = {}
    for name, data in files:
        ext = Path(name).suffix.lower()
        stem = _pair_key(name)
        entry = by_stem.setdefault(stem, {"name": Path(name).name, "json": None, "images": []})
        if ext in _pc.JSON_EXTS:
            entry["json"] = (name, data)
        elif ext in _pc.IMAGE_EXTS:
            entry["images"].append((name, data))

    candidates: list[dict] = []
    seen_hashes: set[str] = set()
    # Existing library hashes (cross-scan dedupe).
    async with async_session() as s:
        rows = (await s.execute(select(PoseLibrary.dedup_hash))).scalars().all()
        lib_hashes = {h for h in rows if h}

    counts = {"keypoints": 0, "openpose_img": 0, "depth": 0, "natural": 0, "sample_only": 0,
              "duplicate": 0, "skipped": 0}

    # Optional vision scan: describe each visual pose with the Ollama vision model.
    _v_urls: list = []
    _v_model = ""
    if run_vision:
        try:
            from backend.database.models import AppSettings as _VAS
            async with async_session() as _vses:
                _vs = (await _vses.execute(select(_VAS).limit(1))).scalars().first()
                if _vs:
                    _v_model = (getattr(_vs, "ollama_vision_model", "") or "").strip()
                    _v_urls = list(getattr(_vs, "ollama_urls", None) or [])
        except Exception as _ve:
            logger.debug(f"pose vision config read failed: {_ve}")
    _VISION_PROMPT = (
        "This is a pose reference (OpenPose skeleton, depth map, mannequin, or a photo). "
        "Give 5-10 short comma-separated tags describing the POSE only: body position "
        "(standing/sitting/kneeling/crouching/lying/action), orientation (front/back/side/three-quarter), "
        "arm and leg positions, and pose type. Output tags only, no sentences."
    )

    for stem, entry in by_stem.items():
        if len(candidates) >= MAX_SCAN:
            break
        joints = None
        source_type = "unknown"
        sample_rel = ""
        source_rel = ""

        # 1) keypoints from JSON
        if entry["json"]:
            jname, jdata = entry["json"]
            try:
                obj = json.loads(jdata.decode("utf-8", "ignore"))
                joints = _pr.openpose_obj_to_joints(obj)
            except Exception:
                joints = None
            if joints:
                source_type = "keypoints"

        # 2) a paired image is the sample (free thumbnail); classify it too
        cand_id = uuid4().hex[:12]
        if entry["images"]:
            iname, idata = entry["images"][0]
            sample_path = scan_tmp / f"{cand_id}_sample{Path(iname).suffix.lower()}"
            try:
                sample_path.write_bytes(idata)
                sample_rel = sample_path.name
                img_kind = _pc.classify_path(sample_path)
                if source_type == "unknown":
                    source_type = img_kind if img_kind != "natural" else "sample_only"
                source_rel = sample_path.name
            except Exception:
                sample_rel = ""

        if joints is None and not sample_rel:
            counts["skipped"] += 1
            continue

        # Dedupe on pose shape (only when we have joints).
        h = _pc.dedup_hash(joints) if joints else ""
        dup = bool(h) and (h in seen_hashes or h in lib_hashes)
        if h:
            seen_hashes.add(h)
        if dup:
            counts["duplicate"] += 1

        # Thumbnail: sample image if present, else render the 2D mannequin.
        thumb_name = f"{cand_id}_thumb.png"
        thumb_path = scan_tmp / thumb_name
        try:
            if sample_rel:
                # reuse the sample as the thumbnail
                thumb_name = sample_rel
            elif joints:
                _pr.render_pose(joints, thumb_path, style="mannequin")
            else:
                thumb_name = ""
        except Exception as e:
            logger.debug(f"pose scan thumbnail failed for {stem}: {e}")
            thumb_name = sample_rel or ""

        tags = _pc.auto_tags_from_joints(joints) if joints else ["image-only"]
        if run_vision and _v_urls and _v_model and sample_rel:
            try:
                from backend.services.llm.vision import caption_image_sync as _cap
                import re as _re_v
                _cap_txt = await asyncio.to_thread(
                    _cap, str(scan_tmp / sample_rel), _v_urls, _v_model, _VISION_PROMPT, 90.0)
                if _cap_txt:
                    _vt = [t.strip().lower() for t in _re_v.split(r"[,\n]", _cap_txt) if t.strip()][:10]
                    tags = sorted(set(tags + _vt))
            except Exception as _vex:
                logger.debug(f"pose vision caption failed: {_vex}")
        counts[source_type] = counts.get(source_type, 0) + 1
        candidates.append({
            "cand_id": cand_id,
            "name": entry["name"],
            "source_type": source_type,
            "joints": joints or {},
            "has_joints": bool(joints),
            "thumb": thumb_name,
            "sample": sample_rel,
            "auto_tags": tags,
            "dedup_hash": h,
            "duplicate": dup,
        })

    async with async_session() as s:
        scan = await s.get(LibraryScan, scan_id)
        if scan:
            scan.candidates = candidates
            scan.summary = {"total": len(candidates), **counts}
            scan.status = "ready"
            s.add(scan)
            await s.commit()
    logger.info(f"Pose scan {scan_id} ready: {len(candidates)} candidates")


@router.post("/pose-organizer/scan")
async def pose_scan(file: Optional[UploadFile] = File(None), folder: str = Form(""),
                    run_vision: bool = Form(False),
                    session: AsyncSession = Depends(get_session)):
    """Start a scan of a server folder path OR an uploaded zip. Runs in the
    background; poll GET /pose-organizer/scan/{id}."""
    files: list[tuple[str, bytes]] = []
    source = ""
    if file is not None:
        raw = await file.read()
        source = file.filename or "upload.zip"
        for name, reader in _iter_zip_files(raw):
            files.append((name, reader()))
            if len(files) >= MAX_SCAN * 2:
                break
    elif folder.strip():
        fp = Path(folder.strip())
        if not fp.exists() or not fp.is_dir():
            raise HTTPException(400, f"Folder not found on the server: {folder}")
        source = str(fp)
        for name, reader in _iter_folder_files(fp):
            if Path(name).suffix.lower() in (_pc.JSON_EXTS | _pc.IMAGE_EXTS):
                files.append((name, reader()))
            if len(files) >= MAX_SCAN * 2:
                break
    else:
        raise HTTPException(400, "Provide a zip 'file' or a server 'folder' path")

    if not files:
        raise HTTPException(400, "No .json / image files found to scan")

    scan = LibraryScan(tool="pose", source=source, status="scanning")
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    _scan_task = asyncio.create_task(_run_pose_scan(scan.id, files, run_vision))
    _BG_TASKS.add(_scan_task)  # strong ref — a bare create_task can be GC'd mid-scan
    _scan_task.add_done_callback(_BG_TASKS.discard)
    return {"scan_id": str(scan.id), "status": "scanning", "files": len(files)}


@router.get("/pose-organizer/scan/{scan_id}")
async def pose_scan_status(scan_id: UUID, offset: int = 0, limit: int = 120,
                           session: AsyncSession = Depends(get_session)):
    scan = await session.get(LibraryScan, scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")
    cands = scan.candidates or []
    return {"scan_id": str(scan.id), "status": scan.status, "summary": scan.summary or {},
            "total": len(cands), "candidates": cands[offset:offset + limit]}


@router.get("/pose-organizer/scan/{scan_id}/thumb/{name}")
async def pose_scan_thumb(scan_id: UUID, name: str):
    p = _SCAN_DIR / str(scan_id) / name
    if not p.exists() or ".." in name:
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(p))


class PoseCommitIn(BaseModel):
    cand_ids: Optional[list[str]] = None   # None = all non-duplicate keypoint candidates
    category: str = "Imported"
    extra_tags: list[str] = []
    include_duplicates: bool = False


@router.post("/pose-organizer/scan/{scan_id}/commit")
async def pose_commit(scan_id: UUID, body: PoseCommitIn,
                      session: AsyncSession = Depends(get_session)):
    _ensure_dirs()
    scan = await session.get(LibraryScan, scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")
    cands = {c["cand_id"]: c for c in (scan.candidates or [])}
    pick = body.cand_ids if body.cand_ids is not None else list(cands.keys())
    added = 0
    scan_tmp = _SCAN_DIR / str(scan_id)
    for cid in pick:
        c = cands.get(cid)
        if not c:
            continue
        if not c.get("has_joints"):
            continue  # library entries need keypoints (image-only handled via extract later)
        if c.get("duplicate") and not body.include_duplicates:
            continue
        pl = PoseLibrary(
            name=(c.get("name") or "pose")[:80],
            category=(body.category or "Imported").strip() or "Imported",
            tags=sorted(set((c.get("auto_tags") or []) + list(body.extra_tags))),
            joints=c.get("joints") or {},
            source_type=c.get("source_type") or "keypoints",
            dedup_hash=c.get("dedup_hash") or "",
            provenance={"scan": str(scan_id), "source": scan.source, "file": c.get("name")},
            meta={},
        )
        session.add(pl)
        await session.flush()
        # Thumbnail: copy sample if present, else render the mannequin.
        thumb_out = _POSE_THUMBS / f"{pl.id}.png"
        try:
            src_thumb = scan_tmp / (c.get("thumb") or "")
            if c.get("thumb") and src_thumb.exists():
                shutil.copy2(str(src_thumb), str(thumb_out))
            else:
                _pr.render_pose(pl.joints, thumb_out, style="mannequin")
            pl.thumbnail_rel = thumb_out.name
        except Exception as e:
            logger.debug(f"commit thumbnail failed: {e}")
        added += 1
    scan.status = "committed"
    session.add(scan)
    await session.commit()
    return {"added": added}


# ── Pose Library ────────────────────────────────────────────────────────────
def _pose_out(p: PoseLibrary) -> dict:
    return {"id": str(p.id), "name": p.name, "category": p.category, "tags": p.tags or [],
            "source_type": p.source_type, "has_thumb": bool(p.thumbnail_rel),
            "has_joints": bool(p.joints), "created_at": p.created_at.isoformat() if p.created_at else None}


@router.get("/pose-library")
async def pose_library_list(category: str = "", tag: str = "", q: str = "",
                            offset: int = 0, limit: int = 120,
                            session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(PoseLibrary).order_by(PoseLibrary.created_at.desc()))).scalars().all()
    ql = q.strip().lower()
    out = []
    for p in rows:
        if category and p.category != category:
            continue
        if tag and tag not in (p.tags or []):
            continue
        if ql and ql not in (p.name or "").lower() and not any(ql in t for t in (p.tags or [])):
            continue
        out.append(p)
    return {"total": len(out), "items": [_pose_out(p) for p in out[offset:offset + limit]]}


@router.get("/pose-library/facets")
async def pose_library_facets(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(PoseLibrary))).scalars().all()
    cats: dict[str, int] = {}
    tags: dict[str, int] = {}
    for p in rows:
        cats[p.category] = cats.get(p.category, 0) + 1
        for t in (p.tags or []):
            tags[t] = tags.get(t, 0) + 1
    return {"total": len(rows),
            "categories": sorted(({"name": k, "count": v} for k, v in cats.items()), key=lambda x: -x["count"]),
            "tags": sorted(({"name": k, "count": v} for k, v in tags.items()), key=lambda x: -x["count"])}


@router.get("/pose-library/{pose_id}/thumbnail")
async def pose_library_thumb(pose_id: UUID, session: AsyncSession = Depends(get_session)):
    p = await session.get(PoseLibrary, pose_id)
    if not p:
        raise HTTPException(404, "Pose not found")
    fp = _POSE_THUMBS / (p.thumbnail_rel or f"{p.id}.png")
    if not fp.exists() and p.joints:
        _ensure_dirs()
        try:
            _pr.render_pose(p.joints, fp, style="mannequin")
            p.thumbnail_rel = fp.name
            session.add(p)
            await session.commit()
        except Exception:
            raise HTTPException(404, "No thumbnail")
    if not fp.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(str(fp))


@router.get("/pose-library/{pose_id}/control")
async def pose_library_control(pose_id: UUID, style: str = "openpose",
                               session: AsyncSession = Depends(get_session)):
    p = await session.get(PoseLibrary, pose_id)
    if not p or not p.joints:
        raise HTTPException(404, "Pose has no keypoints")
    _ensure_dirs()
    out = _POSE_THUMBS.parent / "control" / f"{p.id}_{style}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    _pr.render_pose(p.joints, out, style=("openpose" if style == "openpose" else "mannequin"))
    return FileResponse(str(out))


class PosePatchIn(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None


@router.patch("/pose-library/{pose_id}")
async def pose_library_patch(pose_id: UUID, body: PosePatchIn,
                             session: AsyncSession = Depends(get_session)):
    p = await session.get(PoseLibrary, pose_id)
    if not p:
        raise HTTPException(404, "Pose not found")
    if body.name is not None:
        p.name = body.name.strip()[:80]
    if body.category is not None:
        p.category = body.category.strip() or "Uncategorized"
    if body.tags is not None:
        p.tags = sorted(set(t.strip() for t in body.tags if t.strip()))
    session.add(p)
    await session.commit()
    return {"ok": True}


class PoseBulkIn(BaseModel):
    ids: list[str]


@router.post("/pose-library/delete")
async def pose_library_delete(body: PoseBulkIn, session: AsyncSession = Depends(get_session)):
    n = 0
    for i in body.ids:
        try:
            p = await session.get(PoseLibrary, UUID(i))
        except Exception:
            p = None
        if p:
            try:
                (_POSE_THUMBS / (p.thumbnail_rel or "")).unlink(missing_ok=True)
            except Exception:
                pass
            await session.delete(p)
            n += 1
    await session.commit()
    return {"deleted": n}


@router.post("/pose-library/to-presets")
async def pose_library_to_presets(body: PoseBulkIn, session: AsyncSession = Depends(get_session)):
    """Bridge selected library poses into the Character Studio custom-pose store
    so they appear as pickable presets on a character's Poses tab."""
    from backend.api.character_studio import _load_custom_poses, _save_custom_poses
    d = _load_custom_poses()
    added = 0
    for i in body.ids:
        try:
            p = await session.get(PoseLibrary, UUID(i))
        except Exception:
            p = None
        if not p or not p.joints:
            continue
        name = p.name or "pose"
        slug = "custom_" + ("".join(ch for ch in name.lower() if ch.isalnum() or ch == "_")[:24] or "pose")
        base, k = slug, 2
        while slug in d:
            slug = f"{base}_{k}"; k += 1
        d[slug] = {"name": name, "joints": p.joints, "category": p.category or "Library"}
        added += 1
    _save_custom_poses(d)
    return {"added": added}


@router.get("/pose-library/export")
async def pose_library_export(session: AsyncSession = Depends(get_session)):
    """Export the whole pose library as a portable .zip pack (keypoints + tags
    + thumbnails + a manifest) that another install can import."""
    _ensure_dirs()
    rows = (await session.execute(select(PoseLibrary))).scalars().all()
    out = _LIB_ROOT / "pose_library_pack.zip"
    manifest = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in rows:
            manifest.append({"name": p.name, "category": p.category, "tags": p.tags or [],
                             "joints": p.joints or {}, "source_type": p.source_type,
                             "thumb": f"thumbs/{p.thumbnail_rel}" if p.thumbnail_rel else ""})
            tp = _POSE_THUMBS / (p.thumbnail_rel or "")
            if p.thumbnail_rel and tp.exists():
                zf.write(str(tp), f"thumbs/{p.thumbnail_rel}")
        zf.writestr("pose_pack.json", json.dumps({"version": 1, "poses": manifest}, indent=1))
    return FileResponse(str(out), filename="pose_library_pack.zip")


@router.post("/pose-library/import")
async def pose_library_import(file: UploadFile = File(...),
                              session: AsyncSession = Depends(get_session)):
    """Import a pose pack (.zip produced by export, or any zip whose
    pose_pack.json lists poses)."""
    _ensure_dirs()
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        manifest = json.loads(zf.read("pose_pack.json").decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Not a valid pose pack (missing pose_pack.json)")
    added = 0
    for item in manifest.get("poses", []):
        joints = item.get("joints") or {}
        if not joints:
            continue
        pl = PoseLibrary(
            name=(item.get("name") or "pose")[:80],
            category=(item.get("category") or "Imported").strip() or "Imported",
            tags=item.get("tags") or [],
            joints=joints, source_type=item.get("source_type") or "keypoints",
            dedup_hash=_pc.dedup_hash(joints), provenance={"import": file.filename}, meta={})
        session.add(pl)
        await session.flush()
        thumb_out = _POSE_THUMBS / f"{pl.id}.png"
        try:
            if item.get("thumb"):
                thumb_out.write_bytes(zf.read(item["thumb"]))
            else:
                _pr.render_pose(joints, thumb_out, style="mannequin")
            pl.thumbnail_rel = thumb_out.name
        except Exception:
            pass
        added += 1
    await session.commit()
    return {"imported": added}


# ── Expression Library ──────────────────────────────────────────────────────
def _expr_out(e: ExpressionLibrary) -> dict:
    return {"id": str(e.id), "name": e.name, "category": e.category, "tags": e.tags or [],
            "natural_prompt": e.natural_prompt, "has_thumb": bool(e.thumbnail_rel or e.reference_image_rel),
            "source_type": e.source_type, "created_at": e.created_at.isoformat() if e.created_at else None}


@router.post("/expression-library/import-catalog")
async def expression_import_catalog(session: AsyncSession = Depends(get_session)):
    """Seed the Expression Library from the bundled VNCCS 157-emotion catalog."""
    raw = load_catalog("emotions")
    existing = {(e.name or "").lower() for e in (await session.execute(select(ExpressionLibrary))).scalars().all()}
    added = 0
    items = raw.items() if isinstance(raw, dict) else []
    for category, entries in items:
        if not isinstance(entries, list):
            continue
        for ent in entries:
            if not isinstance(ent, dict):
                continue
            name = ent.get("name") or ent.get("safe_name") or ent.get("label") or ""
            if not name or name.lower() in existing:
                continue
            prompt = ent.get("natural_prompt") or ent.get("description") or f"{name} facial expression"
            e = ExpressionLibrary(name=name[:80], category=str(category)[:40] or "Uncategorized",
                                  tags=[], natural_prompt=prompt, source_type="catalog",
                                  dedup_hash="", provenance={"catalog": "vnccs_emotions"}, meta={})
            session.add(e)
            existing.add(name.lower())
            added += 1
    await session.commit()
    return {"imported": added}


class ExprAddIn(BaseModel):
    name: str
    category: str = "Custom"
    natural_prompt: str = ""
    tags: list[str] = []


@router.post("/expression-library")
async def expression_add(body: ExprAddIn, session: AsyncSession = Depends(get_session)):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    e = ExpressionLibrary(name=body.name.strip()[:80], category=(body.category or "Custom").strip(),
                          natural_prompt=body.natural_prompt.strip(),
                          tags=sorted(set(t.strip() for t in body.tags if t.strip())),
                          source_type="manual", dedup_hash="", provenance={}, meta={})
    session.add(e)
    await session.commit()
    await session.refresh(e)
    return _expr_out(e)


@router.get("/expression-library")
async def expression_list(category: str = "", tag: str = "", q: str = "",
                          offset: int = 0, limit: int = 200,
                          session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ExpressionLibrary).order_by(ExpressionLibrary.name))).scalars().all()
    ql = q.strip().lower()
    out = []
    for e in rows:
        if category and e.category != category:
            continue
        if tag and tag not in (e.tags or []):
            continue
        if ql and ql not in (e.name or "").lower() and ql not in (e.natural_prompt or "").lower():
            continue
        out.append(e)
    return {"total": len(out), "items": [_expr_out(e) for e in out[offset:offset + limit]]}


@router.get("/expression-library/facets")
async def expression_facets(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ExpressionLibrary))).scalars().all()
    cats: dict[str, int] = {}
    for e in rows:
        cats[e.category] = cats.get(e.category, 0) + 1
    return {"total": len(rows),
            "categories": sorted(({"name": k, "count": v} for k, v in cats.items()), key=lambda x: -x["count"])}


class ExprPatchIn(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    natural_prompt: Optional[str] = None
    tags: Optional[list[str]] = None


@router.patch("/expression-library/{expr_id}")
async def expression_patch(expr_id: UUID, body: ExprPatchIn,
                           session: AsyncSession = Depends(get_session)):
    e = await session.get(ExpressionLibrary, expr_id)
    if not e:
        raise HTTPException(404, "Expression not found")
    if body.name is not None:
        e.name = body.name.strip()[:80]
    if body.category is not None:
        e.category = body.category.strip() or "Uncategorized"
    if body.natural_prompt is not None:
        e.natural_prompt = body.natural_prompt.strip()
    if body.tags is not None:
        e.tags = sorted(set(t.strip() for t in body.tags if t.strip()))
    session.add(e)
    await session.commit()
    return {"ok": True}


@router.post("/expression-library/delete")
async def expression_delete(body: PoseBulkIn, session: AsyncSession = Depends(get_session)):
    n = 0
    for i in body.ids:
        try:
            e = await session.get(ExpressionLibrary, UUID(i))
        except Exception:
            e = None
        if e:
            await session.delete(e)
            n += 1
    await session.commit()
    return {"deleted": n}


# ── Worker-backed extraction / rendering (GPU ComfyUI) ──────────────────────
def _dispatcher(request: Request):
    return getattr(request.app.state, "comfy_dispatcher", None)


def _pick_worker_client(disp, cap: str):
    if not disp:
        return None, None
    try:
        w = disp.select_worker({cap}, set(), exclude_runpod=True)
    except Exception:
        w = None
    if not w:
        return None, None
    return w, disp.clients.get(w.url)


def _run_prompt_blocking(client, workflow: dict, timeout_s: float = 120.0):
    """Queue a raw workflow on a worker, poll /history, return (outputs, pid).
    Synchronous — call via asyncio.to_thread."""
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


def _dwpose_workflow(image_filename: str, detect_hands: bool, detect_face: bool, resolution: int) -> dict:
    """Minimal DWPose graph: LoadImage → DWPreprocessor → SavePoseKpsAsJsonFile.
    (comfyui_controlnet_aux). Widget names follow the documented node schema;
    if a worker's DWPreprocessor differs, adjust here.)"""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
        "2": {"class_type": "DWPreprocessor", "inputs": {
            "image": ["1", 0],
            "detect_hand": "enable" if detect_hands else "disable",
            "detect_body": "enable",
            "detect_face": "enable" if detect_face else "disable",
            "resolution": int(resolution),
            "bbox_detector": "yolox_l.onnx",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
            "scale_stick_for_xinsr_cn": "disable",
        }},
        "3": {"class_type": "SavePoseKpsAsJsonFile",
              "inputs": {"pose_kps": ["2", 1], "filename_prefix": "tools_dwpose"}},
    }


def _keypoints_from_outputs(client, outputs: dict) -> list:
    """Pull OpenPose objects from history outputs — prefer the node's inline
    openpose_json, else download a saved .json output file."""
    objs: list = []
    for node_out in outputs.values():
        kj = node_out.get("openpose_json") if isinstance(node_out, dict) else None
        if kj:
            # DWPreprocessor's ui.openpose_json is [json.dumps([frame_dict, ...])]:
            # a list whose element(s) are JSON strings encoding a LIST of frames.
            for k in (kj if isinstance(kj, list) else [kj]):
                try:
                    parsed = json.loads(k) if isinstance(k, str) else k
                except Exception:
                    continue
                if isinstance(parsed, list):
                    objs.extend(parsed)          # frames
                elif isinstance(parsed, dict):
                    objs.append(parsed)
            if objs:
                return objs
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("images", "json", "files", "result", "text"):
            for f in (node_out.get(key) or []):
                fn = f.get("filename") if isinstance(f, dict) else None
                if fn and str(fn).lower().endswith(".json"):
                    try:
                        data = client.download_output(fn, f.get("subfolder", ""), f.get("type", "output"))
                        objs.append(json.loads(data.decode("utf-8", "ignore")))
                    except Exception:
                        pass
    return objs


@router.get("/capabilities")
async def tools_capabilities(request: Request):
    d = _dispatcher(request)
    def cap(c):
        try:
            return bool(d) and d.has_capability(c)
        except Exception:
            return False
    return {"dwpose": cap("dwpose"), "klein": cap("klein")}


@router.post("/pose-organizer/extract")
async def pose_extract(request: Request, files: list[UploadFile] = File(...),
                       category: str = Form("Extracted"),
                       detect_hands: bool = Form(True), detect_face: bool = Form(False),
                       session: AsyncSession = Depends(get_session)):
    """Extract real keypoints from arbitrary images (photos / character art /
    skeleton or mannequin renders) via DWPose on a GPU worker, and add them to
    the library. Requires comfyui_controlnet_aux on a worker (dwpose cap)."""
    _ensure_dirs()
    disp = _dispatcher(request)
    if not disp or not disp.has_capability("dwpose"):
        raise HTTPException(409, "No DWPose-capable worker online. Install "
                                 "comfyui_controlnet_aux (DWPreprocessor) on a GPU ComfyUI worker.")
    _worker, client = _pick_worker_client(disp, "dwpose")
    if not client:
        raise HTTPException(409, "DWPose worker unavailable")
    src_dir = _LIB_ROOT / "pose" / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    errors: list[str] = []
    for uf in files:
        raw = await uf.read()
        suffix = Path(uf.filename or "img.png").suffix.lower() or ".png"
        tmp = _SCAN_DIR / f"_extract_{uuid4().hex[:10]}{suffix}"
        try:
            _ensure_dirs()
            tmp.write_bytes(raw)
            up_name = f"tools_pose_{uuid4().hex[:8]}{suffix}"
            await asyncio.to_thread(client.upload_image, str(tmp), up_name)
            wf = _dwpose_workflow(up_name, detect_hands, detect_face, 768)
            outputs, _pid = await asyncio.to_thread(_run_prompt_blocking, client, wf, 150)
            objs = _keypoints_from_outputs(client, outputs)
            got = 0
            for obj in objs:
                joints = _pr.openpose_obj_to_joints(obj)
                if not joints:
                    continue
                pl = PoseLibrary(
                    name=Path(uf.filename or "pose").stem[:80] or "extracted",
                    category=(category or "Extracted").strip() or "Extracted",
                    tags=sorted(set(_pc.auto_tags_from_joints(joints) + ["extracted"])),
                    joints=joints, source_type="dwpose", dedup_hash=_pc.dedup_hash(joints),
                    provenance={"extract": uf.filename}, meta={})
                session.add(pl)
                await session.flush()
                src_rel = f"{pl.id}{suffix}"
                try:
                    shutil.copy2(str(tmp), str(src_dir / src_rel))
                    pl.source_image_rel = src_rel
                except Exception:
                    pass
                try:
                    thumb = _POSE_THUMBS / f"{pl.id}.png"
                    _pr.render_pose(joints, thumb, style="mannequin")
                    pl.thumbnail_rel = thumb.name
                except Exception:
                    pass
                got += 1
                added += 1
            if got == 0:
                errors.append(f"{uf.filename}: no person/keypoints detected")
        except Exception as e:
            errors.append(f"{uf.filename}: {type(e).__name__}: {e}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    await session.commit()
    return {"extracted": added, "errors": errors}


class HdThumbsIn(BaseModel):
    ids: list[str]


@router.post("/pose-library/hd-thumbnails")
async def pose_hd_thumbnails(request: Request, body: HdThumbsIn,
                             session: AsyncSession = Depends(get_session)):
    """Render a clean HD grey-mannequin thumbnail per pose via the Klein
    RefControl Pose LoRA (image 1 = OpenPose skeleton, image 2 = the 2D
    schematic mannequin). Requires a Klein worker + the pose LoRA installed."""
    _ensure_dirs()
    disp = _dispatcher(request)
    if not disp or not disp.has_capability("klein"):
        raise HTTPException(409, "No Klein worker online for HD mannequin rendering.")
    from backend.database.models import AppSettings
    s = (await session.execute(select(AppSettings).limit(1))).scalars().first()
    pose_lora = (getattr(s, "cs_klein_pose_lora", "") or "").strip()
    if not pose_lora:
        raise HTTPException(409, "Set the Klein pose LoRA (Settings → cs_klein_pose_lora) first.")
    _worker, client = _pick_worker_client(disp, "klein")
    if not client:
        raise HTTPException(409, "Klein worker unavailable")
    from backend.services.comfyui.workflow import prepare_klein_workflow
    try:
        from backend.services.comfyui.workflow import flatten_group_nodes, strip_non_essential_nodes
    except Exception:
        flatten_group_nodes = None
        strip_non_essential_nodes = None
    wf_path = str(Path(__file__).resolve().parent.parent.parent / "workflows" / "KLEIN_EDIT_ULTRA_WORKFLOW_2REF.json")
    tmp_dir = _SCAN_DIR / "_hd"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt = ("apply pose from image 1 with reference from image 2. A plain neutral grey wooden "
              "artist mannequin, smooth featureless head, full body, plain light-grey studio "
              "background, soft even lighting, sharp focus.")
    done = 0
    errors: list[str] = []
    for i in body.ids:
        try:
            p = await session.get(PoseLibrary, UUID(i))
        except Exception:
            p = None
        if not p or not p.joints:
            continue
        try:
            skel = tmp_dir / f"{p.id}_skel.png"
            mann = tmp_dir / f"{p.id}_mann.png"
            _pr.render_pose(p.joints, skel, style="openpose")
            _pr.render_pose(p.joints, mann, style="mannequin")
            sk_up = f"hdskel_{uuid4().hex[:8]}.png"
            mn_up = f"hdmann_{uuid4().hex[:8]}.png"
            await asyncio.to_thread(client.upload_image, str(skel), sk_up)
            await asyncio.to_thread(client.upload_image, str(mann), mn_up)
            wf = prepare_klein_workflow(wf_path, prompt, 768, 1152, 12345,
                                        ref_images=[sk_up, mn_up], pose_lora=pose_lora,
                                        pose_lora_strength=0.9)
            if flatten_group_nodes:
                try:
                    wf = flatten_group_nodes(wf)
                except Exception:
                    pass
            if strip_non_essential_nodes:
                try:
                    strip_non_essential_nodes(wf)  # mutates in place, returns removed names
                except Exception:
                    pass
            outputs, _pid = await asyncio.to_thread(_run_prompt_blocking, client, wf, 300)
            img = None
            for node_out in outputs.values():
                if not isinstance(node_out, dict):
                    continue
                for f in (node_out.get("images") or []):
                    fn = str(f.get("filename", "")).lower()
                    if fn.endswith((".png", ".jpg", ".jpeg", ".webp")):
                        img = f
                        break
                if img:
                    break
            if not img:
                errors.append(f"{p.name}: worker produced no image")
                continue
            data = await asyncio.to_thread(client.download_output, img["filename"],
                                           img.get("subfolder", ""), img.get("type", "output"))
            out = _POSE_THUMBS / f"{p.id}.png"
            out.write_bytes(data)
            p.thumbnail_rel = out.name
            p.meta = {**(p.meta or {}), "hd_thumb": True}
            session.add(p)
            done += 1
        except Exception as e:
            errors.append(f"{(p.name if p else i)}: {type(e).__name__}: {e}")
        finally:
            for f in (tmp_dir / f"{i}_skel.png", tmp_dir / f"{i}_mann.png"):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
    await session.commit()
    return {"rendered": done, "errors": errors}


# ── Sample Generation (poses / expressions from our own models) ──────────────
#
# Instead of always sourcing a reference elsewhere, generate candidate images
# with one of our image models — tuned for isolated, full-body (pose) or
# head-and-shoulders (expression) subjects on a plain background — review them
# in a grid, then convert the chosen one(s) into a Pose (DWPose → keypoints) or
# Expression (reference crop) library entry with categories + tags.

SAMPLE_MODELS = ("z_image", "krea2", "anima", "klein")
_BG_TASKS: set = set()  # retain strong refs to background gen tasks

_POSE_SUFFIX = (
    "full body, entire figure visible from head to toe, single person, standing "
    "on a plain seamless white background, even neutral studio lighting, sharp "
    "focus, clear unobstructed pose, no text"
)
_POSE_NEG = ("cropped, close-up, out of frame, cut off, multiple people, cluttered "
             "background, props, furniture, watermark, text, blurry, deformed")
_EXPR_SUFFIX = (
    "head and shoulders portrait, face fully visible and centered, plain neutral "
    "background, soft even lighting, sharp focus, clear facial expression"
)
_EXPR_NEG = ("full body, hands, cropped face, multiple faces, cluttered background, "
             "watermark, text, blurry, deformed")


def _gen_dir(gen_id: str) -> Path:
    return _SCAN_DIR / f"_gen_{gen_id}"


def _read_gen_status(gen_id: str) -> Optional[dict]:
    fp = _gen_dir(gen_id) / "status.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text("utf-8"))
    except Exception:
        return None


def _write_gen_status(gen_id: str, st: dict) -> None:
    d = _gen_dir(gen_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(st), "utf-8")


def _pick_image_worker(disp, model: str):
    """Pick a healthy worker for a t2i model. Klein needs the 'klein' cap;
    the single-image generators (z_image/krea2/anima) run on any worker."""
    if not disp:
        return None, None
    caps = {"klein"} if model == "klein" else set()
    try:
        w = disp.select_worker(caps, set(), exclude_runpod=True)
    except Exception:
        w = None
    if not w:
        return None, None
    return w, disp.clients.get(w.url)


def _prepare_sample_workflow(model: str, prompt: str, negative: str, w: int, h: int, seed: int) -> dict:
    """Build a text-to-image workflow graph for the chosen model."""
    files = {
        "z_image": "ZIMAGE_TURBO_T2I.json",
        "krea2": "KREA2_TURBO_T2I.json",
        "anima": "ANIMA_T2I.json",
        "klein": "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json",
    }
    fname = files.get(model)
    if not fname:
        raise ValueError(f"unknown model {model!r}")
    path = _WORKFLOWS_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"workflow {fname} not found in workflows/")
    if model == "z_image":
        return prepare_zimage_workflow(str(path), prompt, w, h, seed)
    if model == "krea2":
        return prepare_krea2_workflow(str(path), prompt, w, h, seed)
    if model == "anima":
        return prepare_anima_workflow(str(path), prompt, w, h, seed, negative)
    return prepare_klein_workflow(str(path), prompt, w, h, seed)


def _images_from_outputs(outputs: dict) -> list:
    """Return [{filename, subfolder, type}] for produced (non-temp) images."""
    out: list = []
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for f in (node_out.get("images") or []):
            if isinstance(f, dict) and f.get("filename") and f.get("type") != "temp":
                out.append({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                            "type": f.get("type", "output")})
    return out


async def _run_sample_gen(gen_id: str, disp, kind: str, model: str, raw_prompt: str,
                          negative: str, count: int, w: int, h: int, base_seed: int,
                          isolate: bool) -> None:
    """Background task: generate `count` candidate images, saving each into the
    gen dir and updating status.json as it goes."""
    try:
        suffix = _POSE_SUFFIX if kind == "pose" else _EXPR_SUFFIX
        default_neg = _POSE_NEG if kind == "pose" else _EXPR_NEG
        aug_prompt = f"{raw_prompt.strip()}, {suffix}" if isolate else raw_prompt.strip()
        neg = (negative.strip() or default_neg) if isolate else negative.strip()

        st = {"status": "running", "kind": kind, "model": model, "prompt": raw_prompt.strip(),
              "total": count, "done": 0, "images": [], "error": None}
        _write_gen_status(gen_id, st)
        gd = _gen_dir(gen_id)
        errs: list[str] = []
        await _do_sample_loop(gen_id, disp, model, aug_prompt, neg, count, w, h, base_seed, gd, st, errs)
    except Exception as e:  # pragma: no cover — never let the bg task die silently
        logger.error(f"[sample-gen {gen_id}] fatal: {e}")
        try:
            _write_gen_status(gen_id, {"status": "error", "kind": kind, "model": model,
                                       "prompt": raw_prompt.strip(), "total": count, "done": 0,
                                       "images": [], "error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass


def _sample_worker_pool(disp, model: str) -> list:
    """EVERY worker that can run this model, for fanning the batch.

    ⚠ v1.277.2 — this loop used to call `_pick_image_worker` PER IMAGE, which is
    `select_worker` in a loop: `in_flight` is never incremented on this path, so
    it is a constant function and all 8 samples rendered SERIALLY ON ONE BOX
    (the v1.276.45 disease in one more place, found by a fleet audit). Pool up
    front + round-robin + gather, the image_workshop pattern.
    """
    if not disp:
        return []
    need_cap = "klein" if model == "klein" else None
    out: list = []
    try:
        for w in (getattr(disp, "workers", {}) or {}).values():
            if not getattr(w, "healthy", False) or getattr(w, "is_runpod", False):
                continue
            if need_cap and need_cap not in (getattr(w, "capabilities", set()) or set()):
                continue
            cl = disp.clients.get(w.url)
            if cl:
                out.append((w.url, cl))
    except Exception:                                        # noqa: BLE001
        out = []
    if out:
        return out
    _w, c = _pick_image_worker(disp, model)
    return [(getattr(_w, "url", str(_w)), c)] if c else []


async def _do_sample_loop(gen_id: str, disp, model: str, aug_prompt: str, neg: str,
                          count: int, w: int, h: int, base_seed: int, gd: Path, st: dict,
                          errs: list) -> None:
    pool = _sample_worker_pool(disp, model)
    if not pool:
        st["status"] = "error"
        st["error"] = ("klein-capable worker unavailable" if model == "klein"
                       else "no image worker online")
        _write_gen_status(gen_id, st)
        return
    st["workers"] = sorted({u for u, _c in pool})     # WHERE, per the standing rule
    _write_gen_status(gen_id, st)

    async def _one(i: int) -> None:
        url, client = pool[i % len(pool)]              # assigned UP FRONT
        try:
            wf = _prepare_sample_workflow(model, aug_prompt, neg, w, h, base_seed + i)
            outputs, _pid = await asyncio.to_thread(_run_prompt_blocking, client, wf, 300)
            imgs = _images_from_outputs(outputs)
            if not imgs:
                raise RuntimeError("worker produced no image")
            pick = imgs[-1]  # final SaveImage
            data = await asyncio.to_thread(client.download_output, pick["filename"],
                                           pick.get("subfolder", ""), pick.get("type", "output"))
            name = f"{i}.png"
            (gd / name).write_bytes(data)
            st["images"].append({"id": name, "name": name, "worker": url})
        except Exception as e:                                   # noqa: BLE001
            errs.append(f"#{i + 1}: {type(e).__name__}: {e}")
            logger.warning(f"[sample-gen {gen_id}] image {i} failed on {url}: {e}")
        # ⚠ count COMPLETIONS, not the loop index — finishes are out of order
        # now (the v1.276.45 workshop lesson).
        st["done"] = int(st.get("done") or 0) + 1
        st["error"] = "; ".join(errs[-3:]) if errs else None
        _write_gen_status(gen_id, st)

    await asyncio.gather(*(_one(i) for i in range(count)))

    st["status"] = "done" if st["images"] else "error"
    if not st["images"] and not st["error"]:
        st["error"] = "all generations failed"
    _write_gen_status(gen_id, st)


class SampleGenIn(BaseModel):
    kind: str = "pose"                # 'pose' | 'expression'
    prompt: str
    model: str = "z_image"            # z_image | krea2 | anima | klein
    count: int = 4
    width: int = 768
    height: int = 1152
    seed: Optional[int] = None
    negative: str = ""
    isolate: bool = True              # append no-BG / framing directives


@router.post("/sample/generate")
async def sample_generate(request: Request, body: SampleGenIn):
    _ensure_dirs()
    if body.kind not in ("pose", "expression"):
        raise HTTPException(400, "kind must be 'pose' or 'expression'")
    if body.model not in SAMPLE_MODELS:
        raise HTTPException(400, f"model must be one of {', '.join(SAMPLE_MODELS)}")
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    count = max(1, min(int(body.count or 1), 8))
    disp = _dispatcher(request)
    _worker, client = _pick_image_worker(disp, body.model)
    if not client:
        raise HTTPException(409, "No suitable image worker online" +
                            (" (needs the 'klein' capability)" if body.model == "klein" else ""))
    import random
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)
    gen_id = uuid4().hex[:12]
    w = max(256, min(int(body.width or 768), 2048))
    h = max(256, min(int(body.height or 1152), 2048))
    # Write the initial status synchronously so an immediate poll never 404s.
    _write_gen_status(gen_id, {"status": "running", "kind": body.kind, "model": body.model,
                               "prompt": body.prompt.strip(), "total": count, "done": 0,
                               "images": [], "error": None})
    task = asyncio.create_task(_run_sample_gen(gen_id, disp, body.kind, body.model, body.prompt,
                                               body.negative, count, w, h, base_seed, bool(body.isolate)))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return {"gen_id": gen_id, "total": count, "kind": body.kind, "model": body.model}


@router.get("/sample/{gen_id}")
async def sample_status(gen_id: str):
    st = _read_gen_status(gen_id)
    if st is None:
        raise HTTPException(404, "generation not found")
    imgs = [{"id": im["id"], "url": f"/api/tools/sample/{gen_id}/image/{im['id']}"} for im in st.get("images", [])]
    return {"gen_id": gen_id, "status": st.get("status"), "done": st.get("done", 0),
            "total": st.get("total", 0), "kind": st.get("kind"), "model": st.get("model"),
            "prompt": st.get("prompt", ""), "images": imgs, "error": st.get("error")}


@router.get("/sample/{gen_id}/image/{name}")
async def sample_image(gen_id: str, name: str):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "bad name")
    fp = _gen_dir(gen_id) / name
    if not fp.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(fp), media_type="image/png")


class SampleCommitIn(BaseModel):
    kind: str = "pose"
    image_ids: list[str] = []
    category: str = ""
    name: str = ""
    tags: list[str] = []
    natural_prompt: str = ""          # expressions: defaults to the gen prompt
    detect_hands: bool = True         # poses: DWPose hand keypoints
    detect_face: bool = False


@router.post("/sample/{gen_id}/commit")
async def sample_commit(gen_id: str, body: SampleCommitIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    """Turn selected generated images into library entries. Poses go through
    DWPose (real keypoints); expressions store the crop as a reference image."""
    _ensure_dirs()
    st = _read_gen_status(gen_id)
    if st is None:
        raise HTTPException(404, "generation not found")
    gd = _gen_dir(gen_id)
    ids = [i for i in (body.image_ids or []) if (gd / i).exists() and "/" not in i and "\\" not in i and ".." not in i]
    if not ids:
        raise HTTPException(400, "no valid image_ids")
    tags = sorted(set(t.strip() for t in (body.tags or []) if t.strip()))
    added = 0
    errors: list[str] = []

    if body.kind == "pose":
        disp = _dispatcher(request)
        if not disp or not disp.has_capability("dwpose"):
            raise HTTPException(409, "No DWPose-capable worker online (comfyui_controlnet_aux).")
        _worker, client = _pick_worker_client(disp, "dwpose")
        if not client:
            raise HTTPException(409, "DWPose worker unavailable")
        category = (body.category or "Generated").strip() or "Generated"
        for img_id in ids:
            src = gd / img_id
            try:
                up_name = f"tools_gen_{uuid4().hex[:8]}.png"
                await asyncio.to_thread(client.upload_image, str(src), up_name)
                wf = _dwpose_workflow(up_name, body.detect_hands, body.detect_face, 768)
                outputs, _pid = await asyncio.to_thread(_run_prompt_blocking, client, wf, 150)
                objs = _keypoints_from_outputs(client, outputs)
                got = 0
                for obj in objs:
                    joints = _pr.openpose_obj_to_joints(obj)
                    if not joints:
                        continue
                    pl = PoseLibrary(
                        name=(body.name.strip() or f"{category} pose")[:80],
                        category=category,
                        tags=sorted(set(tags + _pc.auto_tags_from_joints(joints) + ["generated"])),
                        joints=joints, source_type="dwpose", dedup_hash=_pc.dedup_hash(joints),
                        provenance={"generated": {"gen_id": gen_id, "model": st.get("model"), "prompt": st.get("prompt")}},
                        meta={})
                    session.add(pl)
                    await session.flush()
                    try:
                        shutil.copy2(str(src), str(_POSE_SOURCES / f"{pl.id}.png"))
                        pl.source_image_rel = f"{pl.id}.png"
                    except Exception:
                        pass
                    try:
                        thumb = _POSE_THUMBS / f"{pl.id}.png"
                        _pr.render_pose(joints, thumb, style="mannequin")
                        pl.thumbnail_rel = thumb.name
                    except Exception:
                        pass
                    got += 1
                    added += 1
                if got == 0:
                    errors.append(f"{img_id}: no person/keypoints detected")
            except Exception as e:
                errors.append(f"{img_id}: {type(e).__name__}: {e}")
        await session.commit()
        return {"added": added, "errors": errors}

    # expression
    category = (body.category or "Custom").strip() or "Custom"
    base_name = body.name.strip() or category
    nat = body.natural_prompt.strip() or st.get("prompt", "").strip()
    for idx, img_id in enumerate(ids):
        try:
            e = ExpressionLibrary(
                name=(f"{base_name} {idx + 1}" if len(ids) > 1 else base_name)[:80],
                category=category, tags=tags, natural_prompt=nat, source_type="image",
                dedup_hash="",
                provenance={"generated": {"gen_id": gen_id, "model": st.get("model"), "prompt": st.get("prompt")}},
                meta={})
            session.add(e)
            await session.flush()
            ref = _EXPR_REFS / f"{e.id}.png"
            shutil.copy2(str(gd / img_id), str(ref))
            e.reference_image_rel = ref.name
            added += 1
        except Exception as ex:
            errors.append(f"{img_id}: {type(ex).__name__}: {ex}")
    await session.commit()
    return {"added": added, "errors": errors}


@router.get("/expression-library/{expr_id}/thumbnail")
async def expression_thumb(expr_id: UUID, session: AsyncSession = Depends(get_session)):
    e = await session.get(ExpressionLibrary, expr_id)
    if not e:
        raise HTTPException(404, "not found")
    for base, rel in ((_EXPR_REFS, e.reference_image_rel), (_EXPR_THUMBS, e.thumbnail_rel)):
        if rel:
            fp = base / rel
            if fp.exists():
                return FileResponse(str(fp), media_type="image/png")
    raise HTTPException(404, "no image")
