"""v1.260 — a training set rendered a nude image and nothing noticed.

FOUND BY READING A CAPTION FIZGIG WROTE.  Its auto-recaptioner described row
0011 as *"a man standing shirtless in a narrow street"*.  I pulled the image.
He is bare-chested, in a street, in a set meant to teach a clothed character.

    our caption:  "a close-up portrait, head and shoulders of rbmndorianv man,
                   facing the camera, with a slight smile, in front of a city
                   street, warm indoor lamp light."

The caption says nothing about clothing, so nothing contradicted anything. The
image simply went into training with no description of the most obvious thing
in it.

WHY IT HAPPENED
    dorian-v1 has `base_mode: null` and `outfits: null`.

    A null base_mode falls through to the character's own setting, which is
    `auto` — v1.217's "newest version of that view wins". dorian's newest front
    base is the STRIPPED one, made by the Strip SET tool. So rows that resolved
    to a base rather than a tagged reference started from a nude image, and with
    no outfit defined the render prompt said nothing about clothes either. Some
    rows came out dressed because they happened to use a tagged reference (22 of
    40); the rest were a coin flip.

    v1.217 built the dressed/stripped toggle for exactly this and defaulted it
    to the character's setting. For a CHARACTER that is right — the Klein 3.0
    panel is where stripping is done. For a TRAINING SET it is not: a LoRA
    learns whatever is in the pixels, and "sometimes nude" is not a thing anyone
    asked this dataset to teach.

THE FIX
    A dataset with no explicit `base_mode` now resolves to **dressed**, not
    `auto`. Choosing `stripped` or `auto` is still possible and still recorded;
    it just has to be chosen.

    And `GET /datasets/{id}/identity-preview` says, per view, WHICH file will be
    used, whether it is dressed, stripped or unknown, and warns when a stripped
    base meets an empty wardrobe — before a render, not after a training run.

WHAT THIS DOES NOT FIX
    Nothing measures whether a rendered person is dressed. `outfit_ok` is the
    vision model and the vision model is not trusted. This closes the
    CONFIGURATION hole that caused it; the detection hole is still open and is
    recorded as such.
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


rep('''def _baseline_sets(ds: dict) -> Dict[str, Tuple[List[Any], List[str]]]:''',
    '''def _ds_base_mode(ds: dict) -> str:
    """Which base a DATASET renders from.

    v1.260: an unset value used to fall through to the character's own mode,
    which is `auto` — newest version of a view wins. On a character whose newest
    front base came from the Strip SET tool, that silently rendered a training
    set off a nude image, and row 0011 of dorian-v1 went into training
    bare-chested.

    A character defaulting to `auto` is correct; the Klein 3.0 panel is where
    stripping is chosen. A TRAINING SET defaulting to it is not — a LoRA learns
    whatever is in the pixels. `stripped` and `auto` remain available and are
    recorded when chosen."""
    m = str(ds.get("base_mode") or "").strip().lower()
    return m if m in ("dressed", "stripped", "auto") else "dressed"


def _identity_preview(ds: dict) -> Dict[str, Any]:
    """Which image every view will actually start from, before anything renders.

    The whole class of bug this answers is "the render used a base nobody meant
    it to use", and it was previously only visible by reading `identity` on a
    finished row."""
    mode = _ds_base_mode(ds)
    out: Dict[str, Any] = {"base_mode": mode,
                           "base_mode_source": ("explicit" if ds.get("base_mode")
                                                else "default (v1.260: dressed)"),
                           "outfits": len(ds.get("outfits") or []),
                           "views": {}, "warnings": []}
    try:
        char = _load_char(ds["char_slug"])
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"character not readable: {type(e).__name__}")
        return out
    wanted = sorted({_by_key(ANGLES, it["angle"])[3]
                     for it in ds.get("items", []) if it.get("angle")}) or ["front"]
    for view in wanted:
        try:
            fp, label = _base_for_view(ds["char_slug"], char, view, mode)
        except Exception as e:  # noqa: BLE001
            out["views"][view] = {"error": f"{type(e).__name__}: {e}"}
            continue
        lbl = str(label or "")
        # The label is the only honest signal here: `_base_for_view` writes
        # "stripped"/"nude" into it when that is what won.
        stripped = any(w in lbl.lower() for w in ("strip", "nude", "underwear"))
        out["views"][view] = {"file": fp.name if fp else None, "label": lbl,
                              "exists": bool(fp and fp.exists()),
                              "looks_stripped": stripped}
        if not fp:
            out["warnings"].append(f"{view}: no base or reference at all")
    if mode == "stripped" and not (ds.get("outfits") or []):
        out["warnings"].append(
            "base_mode is STRIPPED and no outfits are defined, so nothing in the render "
            "prompt says what he is wearing — every row will come out undressed. Define a "
            "wardrobe, or set base_mode to dressed.")
    if mode == "auto":
        out["warnings"].append(
            "base_mode is AUTO — the newest version of each view wins, which on a character "
            "with a stripped base means some rows render nude and some do not. This is what "
            "put a bare-chested image into dorian-v1. Choose dressed or stripped.")
    lose = [v for v, d in out["views"].items() if d.get("looks_stripped")]
    if lose and not (ds.get("outfits") or []):
        out["warnings"].append(
            f"the base for {', '.join(lose)} is a stripped version and no outfits are "
            f"defined — those rows will render undressed")
    return out


@router.get("/datasets/{ds_id}/identity-preview")
async def dataset_identity_preview(ds_id: str):
    """Which base every view will start from, and what is wrong with that.

    v1.260. Read-only, instant, and the thing that should be checked before a
    render rather than after a training run."""
    return _identity_preview(_read_ds(ds_id))


def _baseline_sets(ds: dict) -> Dict[str, Tuple[List[Any], List[str]]]:''',
    "identity preview")

# every place that resolved the base for a dataset now goes through one function
rep('''        base, src_label = _base_for_view(ds["char_slug"], char, _view,
                                         ds.get("base_mode"))''',
    '''        base, src_label = _base_for_view(ds["char_slug"], char, _view,
                                         _ds_base_mode(ds))''',
    "render jobs")

rep('''        fp, _lbl = _base_for_view(ds["char_slug"], char, "front", ds.get("base_mode"))
        return fp.read_bytes() if fp and fp.exists() else None''',
    '''        fp, _lbl = _base_for_view(ds["char_slug"], char, "front", _ds_base_mode(ds))
        return fp.read_bytes() if fp and fp.exists() else None''',
    "identity ref png")

rep('''    fp, lbl = _base_for_view(slug, char, "front", ds.get("base_mode"))
    picks["front"].append((fp, lbl))''',
    '''    fp, lbl = _base_for_view(slug, char, "front", _ds_base_mode(ds))
    picks["front"].append((fp, lbl))''',
    "baseline front")

rep('''            bp, blbl = _base_for_view(slug, char, side, ds.get("base_mode"))''',
    '''            bp, blbl = _base_for_view(slug, char, side, _ds_base_mode(ds))''',
    "baseline sides")

# The tworef side base. Missed on the first pass and found by grepping every
# call site rather than trusting the four I remembered — this one feeds slot 2
# of a three-quarter render, so a stripped pick here is just as visible.
rep('''            _sb, _ = _base_for_view(ds["char_slug"], char, ang[3], ds.get("base_mode"))''',
    '''            _sb, _ = _base_for_view(ds["char_slug"], char, ang[3], _ds_base_mode(ds))''',
    "tworef side base")

# and the planner says it out loud
rep('''def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:''',
    '''def _plan_warnings_identity(ds: dict) -> List[str]:
    """v1.260: the identity warnings, folded into the plan response so a
    stripped base and an empty wardrobe are visible at planning time."""
    try:
        return list(_identity_preview(ds).get("warnings") or [])
    except Exception:  # noqa: BLE001 — a warning must never break a plan
        return []


def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:''',
    "plan warnings")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
