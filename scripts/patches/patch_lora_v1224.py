"""v1.224 — the reference image was corrupting the framing verdict.

MEASURED, from his dump (40 images, all re-checked): `framing_off` = 30 of 40.
The model's own words on the failures:

    0002 face      "body build and proportions are different"   -> framing FAILED
    0007 face      "body build and proportions are different"   -> framing FAILED
    0009 headshot  "body build differs", "stature differs"      -> framing FAILED
    0010 headshot  "body proportions are different"             -> framing FAILED
    0012 headshot  "body build differs", "stature differs"      -> framing FAILED
    0013 headshot  "body build differs"                         -> framing FAILED
    0004 face      "extreme close-up shot only shows the face"  -> framing FAILED
    0005 face      "no extreme close-up face shot", "body not visible"
    0008 face      "body not visible", "no hands"               -> framing FAILED
    0014 headshot  "close-up framing"                           -> framing FAILED

Every one of those is a complaint about the BODY, on a shot that is a face or a
head-and-shoulders crop.  v1.212 started sending the character's reference as
image 1 so the model could judge identity; it then judged FRAMING against the
reference's framing too.  A close-up cannot win that comparison — 0004 was failed
for being exactly what a `face` shot is.

v1.218 handed identity to ArcFace and made this pure downside: the reference is
still sent, `identity_score_llm` is still requested, and the answer is thrown
away — while the framing verdict it corrupts is kept.

So: the vision model now sees ONE image, the shot, and judges only what a single
image can support — framing, angle, expression, one-person, face-clarity,
artifacts, crop, outfit.  ArcFace owns identity.  Each tool answers the question
it is actually good at, which was the whole argument for running both.

Side effect: QC sends half the pixels, so it should run meaningfully faster.
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


# ── 1. one image in, and say so ────────────────────────────────────────────
rep('''                shot = _wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())
                # reference FIRST so "image 1 / image 2" in the prompt lines up
                imgs = ([_wiz.image_bytes_to_b64(ref_png), shot] if ref_png else [shot])
                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM,
                    _qc_prompt(item, with_identity=bool(ref_png),
                               outfit=_outfit_text(cur, item)), imgs, 0.1, 180.0, True)''',
    '''                # v1.224: ONE image. Sending the character's reference alongside
                # made the model judge framing against the REFERENCE's framing —
                # 30 of 40 images failed framing on complaints like "body build
                # differs" and "body not visible", on face crops. ArcFace owns
                # identity now, so the reference buys nothing and costs that.
                imgs = [_wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())]
                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM,
                    _qc_prompt(item, outfit=_outfit_text(cur, item)),
                    imgs, 0.1, 180.0, True)''',
    "qc: single image")

# ── 2. stop asking for a judgement we discard ──────────────────────────────
rep('''                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))
                    try:                 # kept for comparison, NOT for the trainer
                        flags["identity_score_llm"] = max(0.0, min(1.0, float(
                            data.get("identity_score", 1.0 if flags["same_person"] else 0.0))))
                    except (TypeError, ValueError):
                        flags["identity_score_llm"] = 1.0 if flags["same_person"] else 0.0
                # v1.218: the NUMBER comes from ArcFace, never from the LLM.''',
    '''                # v1.218/v1.224: the identity NUMBER comes from ArcFace, and the
                # vision model is no longer asked about identity at all.''',
    "qc: drop the LLM identity fields")

rep('''                if arc is not None and arc < _like.ARC_MATCH:''',
    '''                if arc is None and not baselines:
                    # No objective scorer available. Say so rather than leaving a
                    # silent gap where an identity verdict used to be.
                    flags["identity_note"] = ("not checked — install insightface "
                                              "for objective identity scoring")
                if arc is not None and arc < _like.ARC_MATCH:''',
    "qc: be explicit when identity was not checked")

rep("""                if ref_png and not flags["same_person"]:
                    note = str(data.get("identity_note") or "does not match the reference")
                    issues = [f"identity: {note[:100]}"] + issues
""",
    "",
    "qc: drop the LLM identity note")

# ── 3. the prompt: one image, and the framing is a TARGET not a complaint ──
rep('''def _qc_prompt(item: dict, with_identity: bool = False, outfit: str = "") -> str:''',
    '''# v1.224: the shot type is the TARGET, not a defect. The model kept listing
# "extreme close-up shot only shows the face" as an ISSUE on a row whose whole
# purpose was to be an extreme close-up.
_FRAMING_NOTE = (
    "\\n\\nThis is the ONLY image. Judge it on its own — there is nothing to compare it "
    "against.\\nThe shot type above is what was ASKED FOR, not a fault: if the picture is "
    "that shot type, \\"framing_ok\\" is true. A close-up showing no body is CORRECT for a "
    "face or head-and-shoulders shot, and must not be failed for it. Say nothing about the "
    "person's build, weight, height or proportions — a single image cannot support that "
    "judgement and it is measured elsewhere.")


def _qc_prompt(item: dict, outfit: str = "") -> str:''',
    "qc prompt: framing is the target")

rep('''            + (_IDENTITY_LINE if with_identity else "")
    ex = _by_key(EXPRESSIONS, item["expression"])''',
    '''            + _FRAMING_NOTE
    ex = _by_key(EXPRESSIONS, item["expression"])''',
    "qc prompt: back branch")

rep('''off by the frame edge. List every problem you see in "issues".{o_line}"""''' + ''' + (
        _IDENTITY_LINE if with_identity else "")''',
    '''off by the frame edge. List every problem you see in "issues".{o_line}""" + _FRAMING_NOTE''',
    "qc prompt: normal branch")

# ── 4. the reference is no longer needed for QC ────────────────────────────
rep('''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None) -> None:''',
    '''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None) -> None:
    # ref_png is accepted and IGNORED since v1.224 — kept so the two callers do
    # not need touching, and so an old caller cannot silently reintroduce it.
    ref_png = None''',
    "qc: ignore ref_png")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
