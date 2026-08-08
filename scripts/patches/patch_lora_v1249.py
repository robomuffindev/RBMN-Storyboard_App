"""v1.249 — the face reference is withheld from exactly the shots that lose the face.

I looked at redv1's failures next to its reference. **ArcFace is right.** 0012
and 0015 are a visibly different woman — rounder, younger, a different nose and
jaw. 0.19 is not a scoring artifact, it is an accurate report that the render
drifted off the character.

Then I read `_render_jobs`:

    face = _refs_by_tag(char, "face")
    if it["framing"] in ("face", "headshot") and face:
        refs.append(face_ref)          # close-ups get the face reference too

**Close-ups get the face reference. `upper` and `full` do not.** And redv1's
reference is a wide full-body photograph in which the face occupies roughly a
twelfth of the frame height. On an `upper` or `full` row Klein is handed that
one small face and asked to re-pose her, so it invents the detail it cannot see.

Which rows failed:

    0012  upper  three_quarter_left   0.1905    no face reference
    0015  full   three_quarter_right  0.1994    no face reference
    0019  full   three_quarter_right  0.2146    no face reference
    0003  face   profile_left         0.2312    HAS the face reference

Three of four are exactly the framings that are denied it. dorian's set does not
show this because his front base is a tighter shot — his face is a larger share
of the reference to begin with.

WHAT SHIPS
    `face_ref` — "closeups" (today's behaviour, still the default) · "always" ·
    "never". Nothing moves until it is measured; `scripts\\faceref_test.ps1`
    renders redv1's twelve upper and full rows both ways and compares identity
    medians. If "always" wins it becomes the default in its own version, with
    the numbers in the changelog.

    Not defaulted on faith, however obvious it looks. The last two things that
    looked obvious — the framing question and the crop top-edge rule — were both
    wrong, and both were caught by measuring first.

ALSO: `below_match` becomes a dataset-level warning.
    redv1's export shipped 20 of 20 with `flagged: 0` — and 7 of 18 scored faces
    below `ARC_MATCH`, and a minimum of 0.266 that only cleared the
    different-person floor on the fifth draw. "Nothing flagged" read as "this is
    a good dataset". It is not. A set where a third of the faces are under the
    match line now says so where it cannot be missed.
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


# ── 1. the option ────────────────────────────────────────────────────────────
rep('''TQ_DEFAULT = "auto"''',
    '''TQ_DEFAULT = "auto"

# v1.249: which shot types get the character's FACE reference alongside the base.
#   closeups  face and headshot only — the behaviour every dataset so far used
#   always    every framing, including upper and full
#   never     none, for a character whose face reference is poor
# Measured motivation: redv1's three worst identity scores (0.19, 0.20, 0.21)
# are all `upper` or `full` rows, which are exactly the ones denied it, and its
# base reference is a wide full-body shot where the face is a twelfth of the
# height. Default unchanged until `scripts\\faceref_test.ps1` says otherwise.
FACE_REF_MODES = ("closeups", "always", "never")
FACE_REF_DEFAULT = "closeups"
_FACE_REF_FRAMINGS = {"closeups": ("face", "headshot"),
                      "always": ("face", "headshot", "upper", "full"),
                      "never": ()}


def _face_ref_mode(ds: dict) -> str:
    m = str((ds.get("options") or {}).get("face_ref") or FACE_REF_DEFAULT).lower()
    return m if m in FACE_REF_MODES else FACE_REF_DEFAULT


def _wants_face_ref(ds: dict, framing: Optional[str]) -> bool:
    return str(framing or "") in _FACE_REF_FRAMINGS[_face_ref_mode(ds)]''',
    "face ref option")

rep('''        face = _refs_by_tag(char, "face")
        if it["framing"] in ("face", "headshot") and face:''',
    '''        face = _refs_by_tag(char, "face")
        # v1.249: which framings get it is an OPTION now. Denying it to `upper`
        # and `full` is where redv1's identity collapsed — those rows see only a
        # wide base in which the face is a twelfth of the frame.
        if _wants_face_ref(ds, it["framing"]) and face:''',
    "job builder")

rep('''                     "tq_wording": (_tq_mode(ds, it["angle"])
                                    if it["angle"] in _TQ_ANGLES else None)})''',
    '''                     "tq_wording": (_tq_mode(ds, it["angle"])
                                    if it["angle"] in _TQ_ANGLES else None),
                     # Stamped so a dataset holding both variants can be scored.
                     "face_ref": _wants_face_ref(ds, it["framing"])})''',
    "stamp")

rep('''                    if jb.get("tq_wording"):
                        it["tq_wording"] = jb["tq_wording"]''',
    '''                    if jb.get("tq_wording"):
                        it["tq_wording"] = jb["tq_wording"]
                    it["face_ref_used"] = bool(jb.get("face_ref"))''',
    "record")

# ── 2. a route to set it ─────────────────────────────────────────────────────
rep('''class TqWordingIn(BaseModel):''',
    '''class FaceRefIn(BaseModel):
    mode: str


@router.put("/datasets/{ds_id}/face-ref")
async def dataset_face_ref(ds_id: str, body: FaceRefIn):
    """Choose which shot types get the character's face reference.  v1.249.

    Changes the option and nothing else — the next render of an affected row
    picks it up, the shot list is untouched, and no rendered image is at risk."""
    m = str(body.mode or "").lower()
    if m not in FACE_REF_MODES:
        raise HTTPException(422, f"unknown mode '{body.mode}' — one of {list(FACE_REF_MODES)}")
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        ds.setdefault("options", {})["face_ref"] = m
        _write_ds(ds)
    has_face = False
    try:
        has_face = bool(_refs_by_tag(_load_char(ds["char_slug"]), "face"))
    except Exception:  # noqa: BLE001
        pass
    return {"mode": m, "framings": list(_FACE_REF_FRAMINGS[m]),
            "character_has_face_reference": has_face,
            "affects": sum(1 for it in ds.get("items", [])
                           if _wants_face_ref(ds, it.get("framing"))),
            "note": (None if has_face else
                     "this character has NO tagged face reference, so this option "
                     "changes nothing until one is added in Klein 3.0")}


class TqWordingIn(BaseModel):''',
    "route")

# ── 3. a low-likeness dataset is not a clean dataset ─────────────────────────
rep('''    out["unreliable"] = ["expression"]
    return out''',
    '''    out["unreliable"] = ["expression"]
    # v1.249: redv1 exported 20 of 20 with `flagged: 0` while 7 of 18 scored
    # faces sat below ARC_MATCH and the minimum had only cleared the
    # different-person floor on its fifth draw. "Nothing flagged" read as "good
    # dataset". A set whose likeness is broadly weak now says so.
    _sv = sorted(s for s in ((it.get("qc") or {}).get("identity_score")
                             for it in ds.get("items", []))
                 if isinstance(s, (int, float)))
    if _sv:
        _below = sum(1 for s in _sv if s < _like.ARC_MATCH)
        out["likeness_median"] = round(_sv[len(_sv) // 2], 4)
        out["likeness_min"] = round(_sv[0], 4)
        out["below_match"] = _below
        if _below >= max(2, len(_sv) // 5):
            out.setdefault("warnings", []).append(
                f"{_below} of {len(_sv)} scored faces are below the {_like.ARC_MATCH} "
                f"match line (median {out['likeness_median']}, worst "
                f"{out['likeness_min']}). Nothing is flagged, because only "
                f"{_like.ARC_DIFFERENT} fails an image — but a training set this "
                f"far from its own reference will teach the trigger word a face "
                f"that drifts. Check the character's references before training.")
    return out''',
    "likeness warning")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
