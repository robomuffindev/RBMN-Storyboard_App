"""v1.206.0 — POSE DESCRIPTIONS: text alongside the pose image (klein2.py).

Two halves of Lorenzo's ask:
  * a pose created from a PROMPT already knows its description — expose it so
    the render prompt can carry it (a mannequin's limbs land wrong on a body of
    a different build: "hands on hips" became hands on the belly);
  * an image-only pose (pack/upload/openpose) can be DESCRIBED by the vision
    LLM on demand, and that description is stored for the same use.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein2_v1206.py <path-to-klein2.py>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


# ── 1. description helpers + vision prompts ───────────────────────────────
rep(
    '''def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    '''def _pose_desc(rec: dict) -> str:
    """The pose in WORDS, for the render prompt (v1.206).

    A stored `desc` wins (LLM- or hand-written).  Otherwise the description is
    recovered from the pose's own prompt by stripping the mannequin style
    wrapper — prompt-generated poses already carry their description, it was
    just buried in the studio-photo boilerplate."""
    d = str(rec.get("desc") or "").strip()
    if d:
        return d
    pr = str(rec.get("prompt") or "").strip()
    if not pr:
        return ""
    pre, _, post = _POSE_STYLE.partition("{pose}")
    if pre and post and pr.startswith(pre) and pr.endswith(post):
        return pr[len(pre):len(pr) - len(post)].strip(" ,.")
    return pr                      # raw-flag prompts are already pose text


POSE_DESC_SYSTEM = (
    "You describe human BODY POSES for an image generator. You report only the "
    "body position you can see — never identity, clothing, style or background."
)
POSE_DESC_PROMPT = """This image shows a single figure in a pose (it may be a gray mannequin, an
openpose stick-figure skeleton, a depth map, or a photo).

Reply in exactly this format, nothing else:

POSE: <one or two sentences describing ONLY the body position — torso lean and
twist, where each arm and hand is, how each leg is bent or planted, and which
way the head looks. Name body landmarks the pose touches (hips, waist, chest,
thigh, the ground, a wall). Do not mention identity, sex, clothing, colours,
materials, background, lighting, camera or art style.>
FACING: <front|back|left|right — which side of the BODY the camera mostly sees,
judged from the chest and hips, not the head. front if the chest faces the
camera, back if turned away, left if the viewer sees the person's left side,
right for the right side.>"""


def _parse_pose_desc(text: str) -> Tuple[str, str]:
    """(description, facing) out of the vision model's reply — tolerant of a
    model that ignores the format and just writes prose."""
    if not text:
        return "", ""
    desc, facing = "", ""
    for line in str(text).splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("pose:"):
            desc = s.split(":", 1)[1].strip()
        elif low.startswith("facing:"):
            facing = s.split(":", 1)[1].strip()
    if not desc:                    # model ignored the format — take the prose
        desc = " ".join(l.strip() for l in str(text).splitlines()
                        if l.strip() and not l.strip().lower().startswith("facing:"))
    desc = desc.strip().strip('"').strip()
    if len(desc) > 600:
        desc = desc[:600].rsplit(" ", 1)[0] + "…"
    return desc, _norm_view(facing)


def _describe_pose_image(urls, model: str, png: bytes) -> Tuple[str, str]:
    """One vision call on a pose image -> (description, facing).  Never raises."""
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    try:
        out = _wiz.ollama_chat_sync(urls, model, POSE_DESC_SYSTEM, POSE_DESC_PROMPT,
                                    [_wiz.image_bytes_to_b64(png)], 0.2, 180.0, False)
    except Exception as e:  # noqa: BLE001
        logger.warning("klein2 describe pose failed: %s", e)
        return "", ""
    return _parse_pose_desc(out or "")


def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    "description helpers",
)

# ── 2. _pose_public exposes the description ──────────────────────────────
rep(
    '''            "view": it.get("view", ""),          # DOMINANT ANGLE (v1.205)''',
    '''            "view": it.get("view", ""),          # DOMINANT ANGLE (v1.205)
            "desc": _pose_desc(it),              # pose IN WORDS (v1.206)
            "desc_source": it.get("desc_source") or ("prompt" if it.get("prompt") else ""),''',
    "_pose_public desc",
)

# ── 3. update route can set/clear the description ───────────────────────
rep(
    '''    view: Optional[str] = None           # DOMINANT ANGLE ('' clears it)''',
    '''    view: Optional[str] = None           # DOMINANT ANGLE ('' clears it)
    desc: Optional[str] = None           # pose description sent to the render''',
    "PoseUpdateIn desc",
)
rep(
    '''    if body.view is not None:
        rec["view"] = _norm_view(body.view)      # '' clears the angle''',
    '''    if body.view is not None:
        rec["view"] = _norm_view(body.view)      # '' clears the angle
    if body.desc is not None:
        d = body.desc.strip()
        rec["desc"] = d
        rec["desc_source"] = "manual" if d else ""''',
    "pose_update desc",
)

# ── 4. GET /poses exposes the describe run ──────────────────────────────
rep(
    '''            "seed_run": _SEED_RUN or None,
            "batch_run": dict(_BATCH_RUN) if _BATCH_RUN else None}''',
    '''            "seed_run": _SEED_RUN or None,
            "batch_run": dict(_BATCH_RUN) if _BATCH_RUN else None,
            "desc_run": dict(_DESC_RUN) if _DESC_RUN else None}''',
    "poses_list desc_run",
)

# ── 5. the describe batch route ─────────────────────────────────────────
rep(
    '''@router.post("/poses/seed-defaults")''',
    '''_DESC_RUN: Dict[str, Any] = {}      # live status of the LLM describe pass


class PoseDescribeIn(BaseModel):
    ids: Optional[List[str]] = None      # explicit selection …
    category: Optional[str] = None       # … or a whole SET
    overwrite: bool = False              # re-describe poses that already have text
    set_view: bool = True                # also fill an EMPTY dominant angle


@router.post("/poses/describe")
async def poses_describe(body: PoseDescribeIn,
                         session: AsyncSession = Depends(get_session)):
    """Describe pose IMAGES with the vision LLM so image-only poses (packs,
    uploads, openpose skeletons) can carry their pose in WORDS into the render
    — and, while looking, fill in an empty dominant angle.

    Runs in the background across every configured Ollama server (one worker
    thread per server, live per-pose {server,status} in GET /poses.desc_run)."""
    if (_DESC_RUN or {}).get("status") == "running":
        raise HTTPException(409, "a describe pass is already running")
    from backend.api.vnccs_native import _ollama_cfg
    urls, _text_model, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    items = _read_poses()
    sel = set(body.ids or [])
    targets = []
    for it in items:
        if sel and it.get("id") not in sel:
            continue
        if not sel and body.category and (it.get("set") or "Custom") != body.category:
            continue
        if not (_K2_POSES / f"{it['id']}.png").exists():
            continue                     # nothing to look at yet
        if _pose_desc(it) and not body.overwrite:
            continue                     # already has words (prompt or stored)
        targets.append(it["id"])
    if not targets:
        return {"started": False, "note": "no poses need describing "
                                          "(prompt poses already carry their description)"}
    _DESC_RUN.clear()
    _DESC_RUN.update({"status": "running", "done": 0, "total": len(targets),
                      "errors": [], "tasks": {}, "servers": [str(u) for u in urls]})
    name_by_id = {it["id"]: it.get("name", "") for it in items}
    for pid in targets:
        _DESC_RUN["tasks"][pid] = {"name": name_by_id.get(pid, ""), "server": None,
                                   "status": "queued"}

    def _run():
        import queue as _q
        import threading as _th
        jobs: "_q.Queue[str]" = _q.Queue()
        for pid in targets:
            jobs.put(pid)
        lock = _th.Lock()

        def _worker(url: str):
            short = str(url).replace("http://", "").replace("https://", "").rstrip("/")
            while True:
                try:
                    pid = jobs.get_nowait()
                except Exception:  # noqa: BLE001 — empty
                    return
                task = _DESC_RUN["tasks"].get(pid) or {}
                task.update({"server": short, "status": "running"})
                try:
                    png = (_K2_POSES / f"{pid}.png").read_bytes()
                    desc, facing = _describe_pose_image([url], vision_model, png)
                    if not desc:
                        raise RuntimeError("vision model returned no description")
                    with lock:
                        cur = _read_poses()
                        rec = next((x for x in cur if x.get("id") == pid), None)
                        if rec is not None:
                            rec["desc"] = desc
                            rec["desc_source"] = "llm"
                            if body.set_view and facing and not (rec.get("view") or ""):
                                rec["view"] = facing
                            rec["updated_at"] = _now_iso()
                            _write_poses(cur)
                    task["status"] = "done"
                except Exception as e:  # noqa: BLE001
                    task.update({"status": "error", "error": str(e)[:200]})
                    _DESC_RUN["errors"].append(f"{task.get('name') or pid}: {e}")
                    logger.warning("klein2 describe[%s] failed: %s", pid, e)
                _DESC_RUN["done"] = _DESC_RUN.get("done", 0) + 1

        threads = [_th.Thread(target=_worker, args=(u,), daemon=True) for u in urls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _DESC_RUN["status"] = "done" if not _DESC_RUN["errors"] else "done_with_errors"
        logger.info("klein2 describe pass: %d/%d described, %d errors",
                    _DESC_RUN["done"] - len(_DESC_RUN["errors"]), len(targets),
                    len(_DESC_RUN["errors"]))

    import threading as _threading
    _threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "total": len(targets), "servers": len(urls)}


@router.post("/poses/seed-defaults")''',
    "describe route",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
