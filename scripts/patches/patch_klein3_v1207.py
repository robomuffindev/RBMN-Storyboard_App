"""v1.207.0 — body-drift controls: prompt composition, body lock, identity boost.

Lorenzo after v1.206: the hands-on-hips render was close, but the body changed
too much — "almost like he got slimmer and taller".  The Klein edit graph builds
from an EMPTY latent (no denoise to hold structure), so the only real levers are
the PROMPT and the REFERENCES.  This patch:
  * splits the pose text into brief|full|off (brief is the new default — the long
    v1.206 paragraph pushed the identity clauses far from the end),
  * appends a TERMINAL body lock that names the observed drift (no slimming, no
    heightening, no idealizing) — last position, so it is the freshest clause,
  * can inject the character's OWN build/height words (named attributes beat
    "same as image 1"),
  * optional identity BOOST: a second identity image as image 3 (the graph picks
    KLEIN_EDIT_ULTRA_WORKFLOW_3REF automatically),
  * one `_compose_prompt()` used by /generate, /generate-set AND a zero-cost
    /preview-prompt endpoint, so the exact prompt is visible before a run.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3_v1207.py <path-to-klein3.py>
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


# ── 1. prompt fragments + composer ────────────────────────────────────────
rep(
    '''_POSE_TEXT_NOTE = (
    " The pose, in words: {desc}. Put that pose on the person from image 1 using HIS OWN "
    "body: same height, same weight, same belly, same limb thickness as image 1. Image 2's "
    "figure is only a diagram of the pose and may be a completely different build — so place "
    "every hand, arm, foot and knee on the correct part of THIS person's body, not where it "
    "sits on the diagram. Hands on the hips means the hands rest on this person's own hip "
    "bones at the sides of his waist, not on his stomach or chest; a hand on the thigh means "
    "on his own thigh. Keep the limb angles, the balance and the body's facing from image 2."
)''',
    '''# The pose in words — BRIEF is the default (v1.207): the long paragraph pushed the
# identity clauses away from the end of the prompt, and the body drifted.
_POSE_TEXT_BRIEF = " The pose, in words: {desc}."
_POSE_TEXT_FULL = (
    " Image 2's figure is only a diagram of the pose and may be a different build — place "
    "every hand, arm, foot and knee on the correct part of THIS person's body, not where it "
    "sits on the diagram: hands on the hips means resting on his own hip bones at the sides "
    "of his waist, not on his stomach or chest. Keep the limb angles, the balance and the "
    "body's facing from image 2."
)
# TERMINAL clause (always last when on) — names the drift Lorenzo measured by eye
# after v1.206: "slimmer and taller".  Named attributes, per the standing rule.
_BODY_LOCK = (
    " His body must stay exactly as it is in image 1: same weight, same width at the "
    "shoulders, chest, belly, waist and hips, same limb thickness, same height and the same "
    "head-to-body proportion. Do NOT slim him down, do NOT make him taller, thinner, younger, "
    "more athletic or more idealized; do not lengthen his legs, narrow his waist or flatten "
    "his stomach. Only the arms, legs, torso angle and head direction change to form the pose."
)
_BOOST_NOTE = (
    " Image 3 shows the SAME person as image 1 from another view — use image 1 and image 3 "
    "together for his face, hair and body, and image 2 only for the pose."
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
                    body_words: bool = True, boosted: bool = False, extra: str = "") -> str:
    """THE single prompt builder — used by /generate, /generate-set and the
    zero-cost /preview-prompt endpoint, so what the panel shows is what runs.

    Order matters: identity opener -> (diagram note) -> pose in words -> his own
    build -> the user's extra -> BODY LOCK last (freshest clause wins)."""
    prompt = _GEN_PROMPT
    if boosted:
        prompt += _BOOST_NOTE
    if pose.get("source") == "upload" or not (pose.get("prompt") or "").strip():
        prompt += _POSE_DIAGRAM_NOTE
    mode = (pose_text or "brief").strip().lower()
    desc = _pose_desc(pose) if mode in ("brief", "full") else ""
    if desc:
        prompt += _POSE_TEXT_BRIEF.format(desc=desc.rstrip(" ."))
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
    return None''',
    "prompt fragments + composer",
)

# ── 2. single generation uses the composer ───────────────────────────────
rep(
    '''    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity
    describe_pose: bool = True       # also state the pose in WORDS in the prompt''',
    '''    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity
    describe_pose: bool = True       # legacy switch (False == pose_text "off")
    pose_text: str = "brief"         # off | brief | full — how much pose wording
    body_lock: bool = True           # terminal "do not slim/stretch him" clause
    body_words: bool = True          # inject his own build/height words
    identity_boost: bool = False     # add a 2nd identity image as image 3''',
    "GenerateIn options",
)
rep(
    '''    prompt = _GEN_PROMPT
    if pose.get("source") == "upload" or not (pose.get("prompt") or "").strip():
        prompt += _POSE_DIAGRAM_NOTE
    pose_text = _pose_desc(pose) if body.describe_pose else ""
    if pose_text:
        prompt += _POSE_TEXT_NOTE.format(desc=pose_text.rstrip(" ."))
    extra = body.prompt_extra.strip()
    if extra:
        prompt = f"{prompt} {extra}"
''',
    '''    boost_fp = _identity_boost_path(body.slug, c, base) if body.identity_boost else None
    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    pose_text = _pose_desc(pose) if mode in ("brief", "full") else ""
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost_fp is not None,
                             extra=body.prompt_extra)
''',
    "generate composes prompt",
)
rep(
    '''    shutil.copy2(base, gd / "ref_identity.png")
    shutil.copy2(pose_fp, gd / "ref_pose.png")
    ref_paths = [str(gd / "ref_identity.png"), str(gd / "ref_pose.png")]''',
    '''    shutil.copy2(base, gd / "ref_identity.png")
    shutil.copy2(pose_fp, gd / "ref_pose.png")
    ref_paths = [str(gd / "ref_identity.png"), str(gd / "ref_pose.png")]
    ref_names = ["ref_identity.png", "ref_pose.png"]
    if boost_fp is not None:                      # image 3 = same person, other view
        shutil.copy2(boost_fp, gd / "ref_identity2.png")
        ref_paths.append(str(gd / "ref_identity2.png"))
        ref_names.append("ref_identity2.png")''',
    "generate boost ref",
)
rep(
    '''          "width": w, "height": h, "refs": ["ref_identity.png", "ref_pose.png"],
          "created_at": _now()}
    _write_gen(gid, st)''',
    '''          "width": w, "height": h, "refs": ref_names,
          "identity_boost": boost_fp is not None, "pose_text_mode": mode,
          "body_lock": body.body_lock, "body_words": body.body_words,
          "created_at": _now()}
    _write_gen(gid, st)''',
    "generate st options",
)

# ── 3. set / tag runs ────────────────────────────────────────────────────
rep(
    '''    match_angle: bool = True         # per-pose DOMINANT ANGLE identity
    describe_pose: bool = True       # state each pose in WORDS in its prompt''',
    '''    match_angle: bool = True         # per-pose DOMINANT ANGLE identity
    describe_pose: bool = True       # legacy switch (False == pose_text "off")
    pose_text: str = "brief"         # off | brief | full
    body_lock: bool = True
    body_words: bool = True
    identity_boost: bool = False''',
    "GenerateSetIn options",
)
rep(
    '''        prompt = _GEN_PROMPT
        if p.get("source") == "upload" or not (p.get("prompt") or "").strip():
            prompt += _POSE_DIAGRAM_NOTE
        p_text = _pose_desc(p) if body.describe_pose else ""
        if p_text:
            prompt += _POSE_TEXT_NOTE.format(desc=p_text.rstrip(" ."))
        if extra:
            prompt = f"{prompt} {extra}"''',
    '''        p_mode = (body.pose_text or "brief") if body.describe_pose else "off"
        p_text = _pose_desc(p) if p_mode in ("brief", "full") else ""
        prompt = _compose_prompt(c, p, pose_text=p_mode, body_lock=body.body_lock,
                                 body_words=body.body_words, boosted=p_boost is not None,
                                 extra=extra)''',
    "generate-set composes prompt",
)
rep(
    '''        shutil.copy2(p_base or base, gd / "ref_identity.png")''',
    '''        p_boost = (_identity_boost_path(body.slug, c, p_base or base)
                   if body.identity_boost else None)
        shutil.copy2(p_base or base, gd / "ref_identity.png")
        if p_boost is not None:
            shutil.copy2(p_boost, gd / "ref_identity2.png")''',
    "generate-set boost copy",
)
rep(
    '''               "width": w, "height": h, "refs": ["ref_identity.png", "ref_pose.png"],''',
    '''               "width": w, "height": h,
               "refs": (["ref_identity.png", "ref_pose.png"]
                        + (["ref_identity2.png"] if p_boost is not None else [])),
               "identity_boost": p_boost is not None, "pose_text_mode": p_mode,''',
    "generate-set gst refs",
)
rep(
    '''        jobs = [{"key": pid, "prompt": pr,
                 "refs": [str(gd / "ref_identity.png"), str(gd / "ref_pose.png")],
                 "w": w, "h": h, "seed": sd}''',
    '''        jobs = [{"key": pid, "prompt": pr,
                 "refs": ([str(gd / "ref_identity.png"), str(gd / "ref_pose.png")]
                          + ([str(gd / "ref_identity2.png")]
                             if (gd / "ref_identity2.png").exists() else [])),
                 "w": w, "h": h, "seed": sd}''',
    "generate-set job refs",
)

# ── 4. zero-cost prompt preview ─────────────────────────────────────────
rep(
    '''def _gen_public(gid: str, st: dict) -> dict:''',
    '''class PreviewIn(BaseModel):
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
    ident, ident_src = _base_for_view(body.slug, c, pose_view)
    boost = _identity_boost_path(body.slug, c, ident) if body.identity_boost else None
    mode = (body.pose_text or "brief") if body.describe_pose else "off"
    prompt = _compose_prompt(c, pose, pose_text=mode, body_lock=body.body_lock,
                             body_words=body.body_words, boosted=boost is not None,
                             extra=body.prompt_extra)
    return {"prompt": prompt, "words": len(prompt.split()),
            "pose": pose.get("name"), "pose_id": pose.get("id"),
            "pose_view": pose_view, "identity_source": ident_src,
            "pose_desc": _pose_desc(pose),
            "refs": (["image 1: " + ident_src, "image 2: pose"]
                     + (["image 3: second identity view"] if boost else [])),
            "identity_boost": boost is not None}


def _gen_public(gid: str, st: dict) -> dict:''',
    "preview-prompt endpoint",
)
rep(
    '''            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),''',
    '''            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),
            "identity_boost": st.get("identity_boost"), "pose_text_mode": st.get("pose_text_mode"),''',
    "_gen_public options",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
