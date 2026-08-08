"""v1.208.0 — affirmative prompts + BODY-MATCHED pose mannequins (klein3.py).

Two fixes for the body drift Lorenzo still sees:

1. MEASURED: the Klein graph has NO negative-prompt node and runs at cfg=1, so
   every "do NOT make him taller/thinner/more athletic" guard we shipped in
   v1.207 had no mechanism behind it — it only put those words into the
   conditioning.  Every clause is now AFFIRMATIVE, and the exclusion NAMES what
   image 2's body is (build/height/weight/limb thickness) instead of hiding it
   behind category words like "appearance".

2. Lorenzo's idea: reshape the pose mannequin to HIS build first, then render.
   `POST /characters/{slug}/posefit` runs a Klein 2-ref edit (pose image +
   his base) that redraws the mannequin with his proportions while holding the
   pose, caches it per character+pose, and `pose_source="bodyfit"` then feeds
   THAT as image 2 — so image 2 no longer carries a different body at all.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3_v1208.py <path-to-klein3.py>
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


# ── 1. import the scrubber ────────────────────────────────────────────────
rep(
    """    _read_poses, _K2_POSES, _pose_desc,
)""",
    """    _read_poses, _K2_POSES, _pose_desc, _clean_pose_desc,
)""",
    "import _clean_pose_desc",
)

# ── 2. AFFIRMATIVE opener that names image 2's body ──────────────────────
rep(
    '''_GEN_PROMPT = (
    "The person from image 1, now in the exact body pose shown in image 2. "
    "Keep the identity from image 1 exactly: same face, same hairstyle, same "
    "body build and proportions, same clothing. Copy ONLY the body pose from "
    "image 2 — ignore image 2's appearance, material, style and background "
    "entirely. Photorealistic, natural lighting, full body shot, plain "
    "neutral background."
)''',
    '''# v1.208: every clause is AFFIRMATIVE.  This graph has no negative-prompt node
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
)''',
    "affirmative _GEN_PROMPT",
)

# ── 3. affirmative pose-text + body lock ─────────────────────────────────
rep(
    '''_POSE_TEXT_FULL = (
    " Image 2's figure is only a diagram of the pose and may be a different build — place "
    "every hand, arm, foot and knee on the correct part of THIS person's body, not where it "
    "sits on the diagram: hands on the hips means resting on his own hip bones at the sides "
    "of his waist, not on his stomach or chest. Keep the limb angles, the balance and the "
    "body's facing from image 2."
)''',
    '''_POSE_TEXT_FULL = (
    " Image 2 is a diagram of the pose: read the joint angles from it and land every hand, "
    "arm, foot and knee on the matching part of HIS body — hands on the hips rest on his own "
    "hip bones at the sides of his own waist, a hand on the thigh rests on his own thigh. "
    "The balance and the facing come from image 2."
)''',
    "affirmative _POSE_TEXT_FULL",
)
rep(
    '''# TERMINAL clause (always last when on) — names the drift Lorenzo measured by eye
# after v1.206: "slimmer and taller".  Named attributes, per the standing rule.
_BODY_LOCK = (
    " His body must stay exactly as it is in image 1: same weight, same width at the "
    "shoulders, chest, belly, waist and hips, same limb thickness, same height and the same "
    "head-to-body proportion. Do NOT slim him down, do NOT make him taller, thinner, younger, "
    "more athletic or more idealized; do not lengthen his legs, narrow his waist or flatten "
    "his stomach. Only the arms, legs, torso angle and head direction change to form the pose."
)''',
    '''# TERMINAL clause (last when on).  v1.208: stated POSITIVELY — the v1.207 wording
# was a list of "do NOT" guards, which on a cfg=1 graph with no negative
# conditioning just injected "thinner / taller / more athletic" into the prompt.
_BODY_LOCK = (
    " The body in the result is the body from image 1: the same weight, the same width at "
    "the shoulders, the chest, the belly, the waist and the hips, the same limb thickness, "
    "the same stature and the same head-to-body proportion. His arms, his legs, his torso "
    "angle and his head direction are the only things that move to form the pose."
)''',
    "affirmative _BODY_LOCK",
)
rep(
    '''_BOOST_NOTE = (
    " Image 3 shows the SAME person as image 1 from another view — use image 1 and image 3 "
    "together for his face, hair and body, and image 2 only for the pose."
)''',
    '''_BOOST_NOTE = (
    " Image 3 shows the SAME person as image 1 from another view: use image 1 and image 3 "
    "together for his face, his hair and his body, and image 2 for the pose."
)''',
    "affirmative _BOOST_NOTE",
)

# ── 4. compose: scrub the description, note the fitted mannequin ─────────
rep(
    '''    mode = (pose_text or "brief").strip().lower()
    desc = _pose_desc(pose) if mode in ("brief", "full") else ""''',
    '''    mode = (pose_text or "brief").strip().lower()
    # v1.208: build words in the DESCRIPTION pull the render toward that build
    desc = _clean_pose_desc(_pose_desc(pose)) if mode in ("brief", "full") else ""''',
    "compose scrubs the description",
)
rep(
    '''def _compose_prompt(c: dict, pose: dict, pose_text: str = "brief", body_lock: bool = True,
                    body_words: bool = True, boosted: bool = False, extra: str = "") -> str:''',
    '''def _compose_prompt(c: dict, pose: dict, pose_text: str = "brief", body_lock: bool = True,
                    body_words: bool = True, boosted: bool = False, extra: str = "",
                    bodyfit: bool = False) -> str:''',
    "compose signature bodyfit",
)
rep(
    '''    prompt = _GEN_PROMPT
    if boosted:
        prompt += _BOOST_NOTE''',
    '''    prompt = _GEN_PROMPT
    if bodyfit:
        prompt += _BODYFIT_NOTE
    if boosted:
        prompt += _BOOST_NOTE''',
    "compose bodyfit note",
)

# ── 5. the body-matched mannequin (Lorenzo's idea) ──────────────────────
rep(
    '''def _identity_boost_path(slug: str, c: dict, primary: Optional[Path]) -> Optional[Path]:''',
    '''# ── Body-matched pose mannequins (v1.208, his idea) ─────────────────────────
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


def _identity_boost_path(slug: str, c: dict, primary: Optional[Path]) -> Optional[Path]:''',
    "posefit prompt + path",
)

# ── 6. posefit routes ───────────────────────────────────────────────────
rep(
    '''class PreviewIn(BaseModel):''',
    '''class PoseFitIn(BaseModel):
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
    if "/" in pose_id or "\\\\" in pose_id or ".." in pose_id:
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
    if "/" in pose_id or "\\\\" in pose_id or ".." in pose_id:
        raise HTTPException(400, "bad id")
    _posefit_path(slug, pose_id).unlink(missing_ok=True)
    return {"deleted": pose_id}


class PreviewIn(BaseModel):''',
    "posefit routes",
)

# ── 7. generation can use the fitted mannequin as image 2 ──────────────
rep(
    '''    body_words: bool = True          # inject his own build/height words
    identity_boost: bool = False     # add a 2nd identity image as image 3''',
    '''    body_words: bool = True          # inject his own build/height words
    identity_boost: bool = False     # add a 2nd identity image as image 3
    pose_source: str = "library"     # library | bodyfit (his body-matched mannequin)''',
    "GenerateIn.pose_source",
)
rep(
    '''    pose_fp = _K2_POSES / f"{body.pose_id}.png"
    if not pose_fp.exists():
        raise HTTPException(409, "pose image missing — regenerate it in the library")
''',
    '''    pose_fp = _K2_POSES / f"{body.pose_id}.png"
    if not pose_fp.exists():
        raise HTTPException(409, "pose image missing — regenerate it in the library")
    fitted = _posefit_path(body.slug, body.pose_id)
    use_fit = body.pose_source == "bodyfit" and fitted.exists()
    if use_fit:
        pose_fp = fitted
''',
    "generate picks the fitted mannequin",
)
rep(
    '''    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost_fp is not None,
                             extra=body.prompt_extra)''',
    '''    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost_fp is not None,
                             extra=body.prompt_extra, bodyfit=use_fit)''',
    "generate passes bodyfit",
)
rep(
    '''          "identity_boost": boost_fp is not None, "pose_text_mode": mode,''',
    '''          "identity_boost": boost_fp is not None, "pose_text_mode": mode,
          "pose_source": "bodyfit" if use_fit else "library",''',
    "generate records pose_source",
)

rep(
    '''    pose_text: str = "brief"         # off | brief | full
    body_lock: bool = True
    body_words: bool = True
    identity_boost: bool = False''',
    '''    pose_text: str = "brief"         # off | brief | full
    body_lock: bool = True
    body_words: bool = True
    identity_boost: bool = False
    pose_source: str = "library"     # library | bodyfit''',
    "GenerateSetIn.pose_source",
)
rep(
    '''        shutil.copy2(_K2_POSES / f"{p['id']}.png", gd / "ref_pose.png")''',
    '''        p_fit = _posefit_path(body.slug, p["id"])
        p_use_fit = body.pose_source == "bodyfit" and p_fit.exists()
        shutil.copy2(p_fit if p_use_fit else _K2_POSES / f"{p['id']}.png",
                     gd / "ref_pose.png")''',
    "generate-set picks the fitted mannequin",
)
rep(
    '''        prompt = _compose_prompt(c, p, pose_text=p_mode, body_lock=body.body_lock,
                                 body_words=body.body_words, boosted=p_boost is not None,
                                 extra=extra)''',
    '''        prompt = _compose_prompt(c, p, pose_text=p_mode, body_lock=body.body_lock,
                                 body_words=body.body_words, boosted=p_boost is not None,
                                 extra=extra, bodyfit=p_use_fit)''',
    "generate-set passes bodyfit",
)
rep(
    '''               "identity_boost": p_boost is not None, "pose_text_mode": p_mode,''',
    '''               "identity_boost": p_boost is not None, "pose_text_mode": p_mode,
               "pose_source": "bodyfit" if p_use_fit else "library",''',
    "generate-set records pose_source",
)

# ── 8. preview reflects both ───────────────────────────────────────────
rep(
    '''    body_words: bool = True
    identity_boost: bool = False


@router.post("/preview-prompt")''',
    '''    body_words: bool = True
    identity_boost: bool = False
    pose_source: str = "library"


@router.post("/preview-prompt")''',
    "PreviewIn.pose_source",
)
rep(
    '''    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost is not None,
                             extra=body.prompt_extra)
    return {"prompt": prompt, "words": len(prompt.split()),''',
    '''    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    fitted = _posefit_path(body.slug, pose["id"])
    use_fit = body.pose_source == "bodyfit" and fitted.exists()
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost is not None,
                             extra=body.prompt_extra, bodyfit=use_fit)
    return {"prompt": prompt, "words": len(prompt.split()),
            "pose_source": "bodyfit" if use_fit else "library",
            "pose_desc_clean": _clean_pose_desc(_pose_desc(pose)),''',
    "preview reports bodyfit + cleaned text",
)
rep(
    '''            "refs": (["image 1: " + ident_src, "image 2: pose"]''',
    '''            "refs": (["image 1: " + ident_src,
                      "image 2: " + ("body-matched mannequin" if use_fit else "pose")]''',
    "preview ref labels",
)
rep(
    '''            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),
            "identity_boost": st.get("identity_boost"), "pose_text_mode": st.get("pose_text_mode"),''',
    '''            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),
            "identity_boost": st.get("identity_boost"), "pose_text_mode": st.get("pose_text_mode"),
            "pose_source": st.get("pose_source"),''',
    "_gen_public pose_source",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
