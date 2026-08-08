"""v1.212.0 — adopt what lora-dataset-studio and Fizgig already solved.

He pointed at two projects doing adjacent work.  Read both; three things are
worth taking, and one of them is a real hole in ours:

1. **IDENTITY CHECK (the hole).**  Both tools score every generated image against
   the character before training — lora-dataset-studio runs InsightFace
   similarity with green/orange triage, Fizgig has a "Look Consistency Filter"
   against 3 reference images.  Our QC checked framing, angle, expression and
   artifacts but never asked *is this still him*, which for a character LoRA is
   the one question that matters: an off-identity image teaches the trigger the
   wrong face.  Added as a second image in the same vision call — the base
   reference and the render, side by side — so it costs no new dependency.
   (InsightFace would be more precise; it also needs Visual Studio Build Tools
   on Windows, and the vision model is already wired and already running.)

2. **FACE-HEAVY COMPOSITION.**  lora-dataset-studio aims at 12 face / 6 bust /
   6 body / 1 back — roughly 48/24/24/4, far more face than our 20/20/30/30.
   Both projects converge there for likeness, so it ships as a PRESET rather
   than a silent change: `balanced` (ours, for full-body flexibility) and
   `face_heavy` (theirs, for likeness).  Measure, then pick.

3. **RANK.**  Fizgig's default is rank 8–16, explicitly "challenging prior
   rank-16 assumptions on 9B-scale models"; our notes said 16–32.  Guidance
   corrected — 16 default, 32 only for a very distinctive face.

Also recorded: Fizgig trains Krea 2 with **NF4 4-bit at ~8.3 GB and fp8 at
~14 GB with NO block swap**, which beats the musubi block-swap route on a 16 GB
card because nothing is bottlenecked on PCIe.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v1212.py <path-to-lora.py>
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


# ── 1. composition presets ───────────────────────────────────────────────
rep(
    '''# Face and headshot rows carry no body pose — captioning a pose the crop cannot''',
    '''# Framing weights.  'balanced' is ours (full-body flexibility); 'face_heavy'
# mirrors lora-dataset-studio's 12 face / 6 bust / 6 body / 1 back target, which
# Fizgig's likeness tooling also leans toward — more face data buys likeness,
# fewer body shots costs some full-body flexibility.  A preset, not a silent
# change: run one of each and look.
FRAMING_PRESETS = {
    "balanced": {"face": 0.20, "headshot": 0.20, "upper": 0.30, "full": 0.30},
    "face_heavy": {"face": 0.44, "headshot": 0.24, "upper": 0.16, "full": 0.16},
}
# how often the camera may face away, per preset
_BACK_EVERY = {"balanced": 10, "face_heavy": 20}

# Face and headshot rows carry no body pose — captioning a pose the crop cannot''',
    "FRAMING_PRESETS",
)
rep(
    '''    # framing allocation by weight, largest-remainder so the total is exact
    raw = [(f, count * f[1]) for f in FRAMINGS]''',
    '''    preset = str(opts.get("preset") or "balanced")
    weights = FRAMING_PRESETS.get(preset, FRAMING_PRESETS["balanced"])
    # framing allocation by weight, largest-remainder so the total is exact
    raw = [(f, count * weights.get(f[0], f[1])) for f in FRAMINGS]''',
    "plan honours the preset",
)
rep(
    '''    keyed = {a[0]: a for a in angles}
    mix = [keyed[k] for k in _ANGLE_MIX if k in keyed] or angles''',
    '''    keyed = {a[0]: a for a in angles}
    mix = [keyed[k] for k in _ANGLE_MIX if k in keyed] or angles
    if _BACK_EVERY.get(preset, 10) > len(mix):        # thin the back rows out
        mix = mix + [k for k in mix if k[0] != "back"]''',
    "back frequency per preset",
)
rep(
    '''    count: int = 40
    outfit: str = ""                 # one outfit for the whole set, or blank = as-is''',
    '''    count: int = 40
    preset: str = "balanced"         # balanced | face_heavy (see FRAMING_PRESETS)
    outfit: str = ""                 # one outfit for the whole set, or blank = as-is''',
    "DatasetIn.preset",
)
rep(
    '''        "options": body.options or {}, "created_at": _now(),
        "items": _build_plan(body.count, body.options or {}),''',
    '''        "options": {**(body.options or {}), "preset": body.preset},
        "preset": body.preset, "created_at": _now(),
        "items": _build_plan(body.count, {**(body.options or {}), "preset": body.preset}),''',
    "dataset stores the preset",
)
rep(
    '''        "framings": [{"key": f[0], "weight": f[1], "caption": f[2], "size": list(f[4])}
                     for f in FRAMINGS],''',
    '''        "framings": [{"key": f[0], "weight": f[1], "caption": f[2], "size": list(f[4])}
                     for f in FRAMINGS],
        "presets": {k: v for k, v in FRAMING_PRESETS.items()},''',
    "recipe exposes presets",
)

# ── 2. the identity check ────────────────────────────────────────────────
rep(
    '''_QC_SYSTEM = ("You are a strict quality checker for image-model training data. "
              "You answer with JSON only.")''',
    '''_QC_SYSTEM = ("You are a strict quality checker for image-model training data. "
              "You answer with JSON only.")

# Both lora-dataset-studio (InsightFace similarity) and Fizgig (Look Consistency
# Filter) score every image against the character before training, and they are
# right to: an off-identity image teaches the trigger word the wrong face, which
# is the one failure a character LoRA cannot survive.  This runs in the SAME
# vision call — image 1 is the character's reference, image 2 is the render.
_IDENTITY_LINE = """
IMAGE 1 is the reference photograph of the character. IMAGE 2 is the generated image
being checked. Add these keys to your JSON:
  "same_person": true/false   — is the person in image 2 the same individual as image 1
                                (face, hair, and especially BODY BUILD and stature)?
  "identity_note": "short phrase"  — if they differ, say how (for example "much slimmer",
                                "different face shape", "younger", "different hair").
Judge build and proportions as carefully as the face: a slimmer or taller version of him
is a different person for this purpose."""''',
    "_IDENTITY_LINE",
)
rep(
    '''def _qc_prompt(item: dict) -> str:''',
    '''def _qc_prompt(item: dict, with_identity: bool = False) -> str:''',
    "qc prompt signature",
)
rep(
    '''Set "angle_ok" false only if the person is facing the camera rather than away from it.
Leave "expression_ok" and "face_clear" true — a hidden face is correct for this shot.
"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
"cropped_badly" means part of the subject this shot type needs is cut off by the frame edge."""''',
    '''Set "angle_ok" false only if the person is facing the camera rather than away from it.
Leave "expression_ok" and "face_clear" true — a hidden face is correct for this shot.
"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
"cropped_badly" means part of the subject this shot type needs is cut off by the frame edge.""" \\
            + (_IDENTITY_LINE if with_identity else "")''',
    "back qc prompt identity",
)
rep(
    '''"artifacts" means deformed hands, extra or missing limbs, melted features, garbled text or
similar defects. "cropped_badly" means part of the subject that this shot type needs is cut
off by the frame edge. List every problem you see in "issues"."""''',
    '''"artifacts" means deformed hands, extra or missing limbs, melted features, garbled text or
similar defects. "cropped_badly" means part of the subject that this shot type needs is cut
off by the frame edge. List every problem you see in "issues".""" + (
        _IDENTITY_LINE if with_identity else "")''',
    "main qc prompt identity",
)
rep(
    '''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict) -> None:''',
    '''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None) -> None:''',
    "qc blocking signature",
)
rep(
    '''                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM, _qc_prompt(item),
                    [_wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())],
                    0.1, 150.0, True)
                data = json.loads(raw) if raw else {}
                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}
                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"])''',
    '''                shot = _wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())
                # reference FIRST so "image 1 / image 2" in the prompt lines up
                imgs = ([_wiz.image_bytes_to_b64(ref_png), shot] if ref_png else [shot])
                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM,
                    _qc_prompt(item, with_identity=bool(ref_png)), imgs, 0.1, 180.0, True)
                data = json.loads(raw) if raw else {}
                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}
                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))
                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                if ref_png and not flags["same_person"]:
                    note = str(data.get("identity_note") or "does not match the reference")
                    issues = [f"identity: {note[:100]}"] + issues
                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"]
                      and flags.get("same_person", True))''',
    "identity in the QC verdict",
)

# ── 3. hand the reference in from both callers ──────────────────────────
rep(
    '''    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st)
            st["status"] = "done"''',
    '''    ref_png = _identity_ref_png(ds)

    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png)
            st["status"] = "done"''',
    "qc route passes the reference",
)
rep(
    '''                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st)''',
    '''                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st,
                             _identity_ref_png(cur))''',
    "repair passes the reference",
)
rep(
    '''def _flagged_ids(ds: dict, include_stuck: bool = False) -> List[str]:''',
    '''def _identity_ref_png(ds: dict) -> Optional[bytes]:
    """The character's front reference, for the side-by-side identity check."""
    try:
        char = _load_char(ds["char_slug"])
        fp, _lbl = _base_for_view(ds["char_slug"], char, "front")
        return fp.read_bytes() if fp and fp.exists() else None
    except Exception:  # noqa: BLE001 — QC still works without it
        return None


def _flagged_ids(ds: dict, include_stuck: bool = False) -> List[str]:''',
    "_identity_ref_png",
)

# ── 4. the breakdown counts identity misses ─────────────────────────────
rep(
    '''           "framing_off": 0, "angle_off": 0, "expression_off": 0,
           "not_one_person": 0, "face_unclear": 0, "stuck": 0, "top_issues": {}}''',
    '''           "framing_off": 0, "angle_off": 0, "expression_off": 0,
           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "stuck": 0, "top_issues": {}}''',
    "summary key",
)
rep(
    '''        if q.get("face_clear") is False:
            out["face_unclear"] += 1''',
    '''        if q.get("face_clear") is False:
            out["face_unclear"] += 1
        if q.get("same_person") is False:
            out["identity_off"] += 1''',
    "summary counts identity",
)

# ── 5. rank guidance corrected ──────────────────────────────────────────
rep(
    '''- rank / alpha: 16-32 (32 for a distinctive face; higher overfits)''',
    '''- rank / alpha: 16 (8-16 is where the Klein/Krea-scale trainers land; 32 only for a very
  distinctive face, and higher overfits)''',
    "rank guidance",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
