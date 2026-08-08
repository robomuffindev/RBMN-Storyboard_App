"""v1.206.0 — the pose in WORDS goes into the render prompt (klein3.py).

Lorenzo's failure case: a very heavy character given a "hands on hips" mannequin
pose came back with his hands on his belly — the mannequin's build is not his,
so copying the image geometry literally puts the hands in the wrong place.  The
prompt now states the pose in words and tells Klein to land it on THIS body.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3_v1206.py <path-to-klein3.py>
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


# ── 1. import the shared description helper ───────────────────────────────
rep(
    """    _read_poses, _K2_POSES,
)""",
    """    _read_poses, _K2_POSES, _pose_desc,
)""",
    "import _pose_desc",
)

# ── 2. the note itself ────────────────────────────────────────────────────
rep(
    '''_POSE_DIAGRAM_NOTE = (''',
    '''_POSE_TEXT_NOTE = (
    " The pose, in words: {desc}. Put that pose on the person from image 1 using HIS OWN "
    "body: same height, same weight, same belly, same limb thickness as image 1. Image 2's "
    "figure is only a diagram of the pose and may be a completely different build — so place "
    "every hand, arm, foot and knee on the correct part of THIS person's body, not where it "
    "sits on the diagram. Hands on the hips means the hands rest on this person's own hip "
    "bones at the sides of his waist, not on his stomach or chest; a hand on the thigh means "
    "on his own thigh. Keep the limb angles, the balance and the body's facing from image 2."
)
_POSE_DIAGRAM_NOTE = (''',
    "_POSE_TEXT_NOTE",
)

# ── 3. single generation ─────────────────────────────────────────────────
rep(
    '''    prompt = _GEN_PROMPT
    if pose.get("source") == "upload" or not (pose.get("prompt") or "").strip():
        prompt += _POSE_DIAGRAM_NOTE
    extra = body.prompt_extra.strip()''',
    '''    prompt = _GEN_PROMPT
    if pose.get("source") == "upload" or not (pose.get("prompt") or "").strip():
        prompt += _POSE_DIAGRAM_NOTE
    pose_text = _pose_desc(pose) if body.describe_pose else ""
    if pose_text:
        prompt += _POSE_TEXT_NOTE.format(desc=pose_text.rstrip(" ."))
    extra = body.prompt_extra.strip()''',
    "generate prompt text",
)
rep(
    '''    count: int = 2
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity''',
    '''    count: int = 2
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity
    describe_pose: bool = True       # also state the pose in WORDS in the prompt''',
    "GenerateIn.describe_pose",
)
rep(
    '''          "pose_view": pose_view, "identity_source": ident_src,
          "total": count, "done": 0, "images": [], "error": None,''',
    '''          "pose_view": pose_view, "identity_source": ident_src, "pose_desc": pose_text,
          "total": count, "done": 0, "images": [], "error": None,''',
    "generate st pose_desc",
)

# ── 4. set / tag runs ────────────────────────────────────────────────────
rep(
    '''    seed: Optional[int] = None
    match_angle: bool = True         # per-pose DOMINANT ANGLE identity''',
    '''    seed: Optional[int] = None
    match_angle: bool = True         # per-pose DOMINANT ANGLE identity
    describe_pose: bool = True       # state each pose in WORDS in its prompt''',
    "GenerateSetIn.describe_pose",
)
rep(
    '''        prompt = _GEN_PROMPT
        if p.get("source") == "upload" or not (p.get("prompt") or "").strip():
            prompt += _POSE_DIAGRAM_NOTE
        if extra:''',
    '''        prompt = _GEN_PROMPT
        if p.get("source") == "upload" or not (p.get("prompt") or "").strip():
            prompt += _POSE_DIAGRAM_NOTE
        p_text = _pose_desc(p) if body.describe_pose else ""
        if p_text:
            prompt += _POSE_TEXT_NOTE.format(desc=p_text.rstrip(" ."))
        if extra:''',
    "generate-set prompt text",
)
rep(
    '''               "pose_view": p_view, "identity_source": p_src,''',
    '''               "pose_view": p_view, "identity_source": p_src, "pose_desc": p_text,''',
    "generate-set gst pose_desc",
)

# ── 5. surface it (gallery / live) ───────────────────────────────────────
rep(
    '''            "set": st.get("set"), "pose_view": st.get("pose_view"),
            "identity_source": st.get("identity_source"),''',
    '''            "set": st.get("set"), "pose_view": st.get("pose_view"),
            "identity_source": st.get("identity_source"), "pose_desc": st.get("pose_desc"),''',
    "_gen_public pose_desc",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
