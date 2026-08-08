"""v1.235 — three-quarter wording becomes a variable, so it can be measured.

WHAT THE MEASUREMENT SAYS (v1.234, 40 images, head yaw)
    three_quarter_left   3 of 8 reach 20 deg.  Every one lands between -17.7
                         and -22.1: the DIRECTION is right, the AMOUNT never is.
    three_quarter_right  4 of 8, bimodal: +21..+37, or -6..-9.5 the wrong way.
    Across the whole dataset there is face data at 0-22 deg and at 56-82, and
    exactly one image in forty above 37.

    The prompt currently says: "his body turned about 45 degrees to his left,
    HEAD TOWARD THE CAMERA".  The renders are obeying it — turned body, front
    head — and head yaw is what a face LoRA learns from.  So this is a prompt
    defect, not a Klein defect, and the fix is wording rather than a workflow.

FOUR WORDINGS, one option key, default unchanged
    degrees  the current text.  The control; nothing moves unless asked.
    frame    frame-relative, and the head turns WITH the body.  "45 degrees" is
             a number a diffusion model has no reason to act on; "the left edge
             of the picture" is a place in the image it can see.
    halfway  a fraction instead of a number, plus a binary physical test
             ("one of his ears is hidden") — Klein acts on NAMED objects, which
             is the same finding that made garment references work.
    tworef   no adjective at all: the front base AND the side base both go in,
             and the prompt asks for halfway between two pictures it can see.
             The most expensive and the most likely to actually land.

Left/right is expressed as an EDGE OF THE PICTURE, never as his left or his
right.  Measured: negative yaw (nose toward the left edge) is what
`three_quarter_left` already produces in 7 of 8 renders, so frame-relative
wording agrees with the behaviour that is already correct, and a handedness
mistake would show up instantly as a sign flip.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. the wordings ──────────────────────────────────────────────────────────
rep('''def _plan_opts(ds: dict) -> dict:''',
    '''# v1.235: how a THREE-QUARTER row is asked for.  One dict so a variant is a
# string in the dataset options rather than an edit to the renderer.
#   {edge}  -> "left" or "right", the edge of the PICTURE his nose points at
#   {n}     -> the 1-based index of the side reference (tworef only)
TQ_WORDINGS = {
    "degrees": ("his body turned about 45 degrees to his {his_side}, "
                "head toward the camera"),
    "frame": ("his head and his body both turned toward the {edge} edge of the "
              "picture, his nose pointing toward the {edge} edge, both of his "
              "eyes still visible to the camera"),
    "halfway": ("his head and his body turned halfway between facing the camera "
                "and facing the {edge} edge of the picture, far enough that one "
                "of his ears is hidden and both of his eyes are still visible"),
    "tworef": ("his head and his body turned halfway between the way he stands "
               "in image 1 and the way he stands in image {n}, both of his eyes "
               "still visible to the camera"),
}
TQ_DEFAULT = "degrees"


def _tq_wording(ds: dict) -> str:
    w = str((ds.get("options") or {}).get("tq_wording") or TQ_DEFAULT).lower()
    return w if w in TQ_WORDINGS else TQ_DEFAULT


def _angle_text(ds: dict, item: dict, ang: Tuple, side_idx: Optional[int] = None) -> str:
    """The sentence describing which way he faces.

    Everything that is not a three-quarter row is returned untouched: front,
    profile and back already measure 4-of-4 and 10-of-10 correct, and the one
    sure way to lose that is to rewrite prompts that are working."""
    key = item.get("angle")
    if key not in _TQ_ANGLES:
        return ang[2]
    mode = _tq_wording(ds)
    edge = "left" if key == "three_quarter_left" else "right"
    if mode == "tworef" and not side_idx:
        # Asked for two references and only got one — say what he faces in
        # words rather than pointing at an image that is not there.
        mode = "halfway"
    return TQ_WORDINGS[mode].format(edge=edge, his_side=edge, n=side_idx or 2)


def _plan_opts(ds: dict) -> dict:''',
    "wordings")

# ── 2. the prompt uses it ────────────────────────────────────────────────────
rep('''def _render_prompt(ds: dict, item: dict, garment_idx: Optional[int] = None) -> str:''',
    '''def _render_prompt(ds: dict, item: dict, garment_idx: Optional[int] = None,
                   side_idx: Optional[int] = None) -> str:''',
    "prompt signature")

rep('''        bits = [
            f"The person from image 1, photographed as {fr[3]}, {ang[2]}, with {ex[1]}.",''',
    '''        bits = [
            f"The person from image 1, photographed as {fr[3]}, "
            f"{_angle_text(ds, item, ang, side_idx)}, with {ex[1]}.",''',
    "prompt uses the wording")

# ── 3. the job builder supplies the second reference ─────────────────────────
rep('''        refs = [str(base)]
        face = _refs_by_tag(char, "face")''',
    '''        refs = [str(base)]
        # v1.235 "tworef": the side base rides along as image 2 so the prompt can
        # ask for halfway between two pictures instead of describing an angle.
        # Only for three-quarter rows, and only when the front base is the one
        # in slot 1 — otherwise there is no pair to be halfway between.
        side_idx = None
        if (it["angle"] in _TQ_ANGLES and _tq_wording(ds) == "tworef"
                and _view == "front"):
            _sb, _ = _base_for_view(ds["char_slug"], char, ang[3], ds.get("base_mode"))
            if _sb and Path(_sb).exists():
                refs.append(str(_sb))
                side_idx = len(refs)     # 1-based, matching the prompt text
        face = _refs_by_tag(char, "face")''',
    "side ref")

rep('''        jobs.append({"key": it["id"], "prompt": _render_prompt(ds, it, g_idx), "refs": refs,''',
    '''        jobs.append({"key": it["id"],
                     "prompt": _render_prompt(ds, it, g_idx, side_idx), "refs": refs,''',
    "pass side_idx")

# ── 4. say which wording produced an image, on the image ─────────────────────
rep('''                    it["status"] = "done"
                    it["identity"] = jb.get("identity")''',
    '''                    it["status"] = "done"
                    it["identity"] = jb.get("identity")
                    # v1.235: which wording rendered THIS image.  Without it a
                    # dataset holding two variants' output cannot be scored,
                    # and the A/B is unreadable the moment anything is re-run.
                    if jb.get("tq_wording"):
                        it["tq_wording"] = jb["tq_wording"]''',
    "record the wording")

rep('''                     "w": it.get("width", 896), "h": it.get("height", 1152),
                     "seed": seed0 + n, "identity": src_label})''',
    '''                     "w": it.get("width", 896), "h": it.get("height", 1152),
                     "seed": seed0 + n, "identity": src_label,
                     "tq_wording": (_tq_wording(ds) if it["angle"] in _TQ_ANGLES
                                    else None)})''',
    "job carries the wording")

# ── 5. a route to set it, so this is not a JSON-editing exercise ─────────────
rep('''@router.post("/datasets/{ds_id}/angles")''',
    '''class TqWordingIn(BaseModel):
    wording: str


@router.put("/datasets/{ds_id}/tq-wording")
async def dataset_tq_wording(ds_id: str, body: TqWordingIn):
    """Choose how three-quarter rows are asked for.  v1.235.

    Changes NOTHING on disk except the option — the next render of a
    three-quarter row picks it up.  Re-planning is neither needed nor wanted:
    the shot list is identical, only the sentence differs, which is exactly
    what makes this measurable."""
    w = str(body.wording or "").lower()
    if w not in TQ_WORDINGS:
        raise HTTPException(422, f"unknown wording '{body.wording}' — "
                                 f"one of {sorted(TQ_WORDINGS)}")
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        ds.setdefault("options", {})["tq_wording"] = w
        _write_ds(ds)
    sample = _angle_text(ds, {"angle": "three_quarter_left"},
                         _by_key(ANGLES, "three_quarter_left"), 2)
    return {"wording": w, "reads": sample,
            "affects": sum(1 for it in ds.get("items", [])
                           if it.get("angle") in _TQ_ANGLES),
            "available": {k: v for k, v in TQ_WORDINGS.items()}}


@router.post("/datasets/{ds_id}/angles")''',
    "route")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
