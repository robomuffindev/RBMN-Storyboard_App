"""Compare the LEFT and RIGHT views of an outfit and list garment differences.

Free: one Ollama vision call, no worker, no GPU. Exists because "the side views
are a little inconsistent" is not a measurable statement, and the side-to-side
continuity work (v1.276.26) needed a before/after that was not my own eyesight.

The right view is MIRRORED before the comparison so both images face the same
way — otherwise the model spends its answer describing the facing difference,
which is the one difference that is supposed to be there.

    python scripts/k3_side_compare.py --char clonejoan --outfit "Red Leather"
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: ⚠ FIRST ATTEMPT FAILED AND IS RECORDED HERE SO IT IS NOT RETRIED.
#: Asking the model to "list the differences" between two images returned an
#: empty list for BOTH baseline outfits — including ones Lorenzo could see
#: differences in. A 7B vision model comparing two images holistically is not a
#: usable instrument: it answers "same costume" and stops.
#:
#: What DOES work is the prompt already proven in `outfits/scan`: describe ONE
#: image into named slots. So each side is scanned separately with that prompt
#: and the two slot dicts are diffed IN CODE, where the comparison is exact and
#: cannot be talked out of a difference.
SYSTEM = (
    "You are a costume supervisor cataloguing a garment for a photo shoot. "
    "You describe only what is visibly present. You never invent items."
)
SLOTS = ("headwear", "eyewear", "outerwear", "top", "underlayer", "belt",
         "bottom", "legwear", "shoes", "gloves", "jewellery", "accessories",
         "carried")
PROMPT = (
    "Look at this image and list ONLY the clothing, footwear and worn or "
    "carried accessories you can actually see.\n\n"
    "Return STRICT JSON with these keys — omit any key whose item is not "
    "visible:\n  " + ", ".join(SLOTS) + "\n\n"
    "Each value is a short noun phrase naming the item with its COLOUR, "
    "MATERIAL and any distinctive detail. Describe the garments only: say "
    "nothing about the person, their pose, their body or the background."
)


def _scan(wiz, urls, model, png: bytes) -> dict:
    out = wiz.ollama_chat_sync(urls, model, SYSTEM, PROMPT,
                               [wiz.image_bytes_to_b64(png)], 0.1, 240.0, True)
    txt = str(out or "").strip()
    if "{" in txt and "}" in txt:
        txt = txt[txt.index("{"): txt.rindex("}") + 1]
    try:
        d = json.loads(txt)
    except Exception:                             # noqa: BLE001
        return {}
    bad = ("none", "n/a", "not visible", "unknown", "nothing")
    return {k: str(d.get(k) or "").strip() for k in SLOTS
            if str(d.get(k) or "").strip()
            and str(d.get(k)).strip().lower() not in bad}


def _words(t: str) -> set:
    import re as _re
    stop = {"a", "an", "the", "with", "and", "of", "in", "on", "her", "his"}
    return {w for w in _re.findall(r"[a-z]+", t.lower()) if w not in stop}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", required=True)
    ap.add_argument("--outfit", required=True)
    ap.add_argument("--variant", default="")
    args = ap.parse_args()

    from backend.config import settings as cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    from PIL import Image

    root = Path(cfg.project_dir) / "_libraries" / "klein3" / "chars" / args.char
    data = json.loads((root / "char.json").read_text("utf-8"))
    got = {}
    for r in data.get("refs", []):
        o = r.get("outfit") or {}
        if (r.get("tag") == "outfit"
                and str(o.get("name") or "") == args.outfit
                and str(o.get("variant") or "") == args.variant
                and str(o.get("view") or "") in ("left", "right")):
            p = root / "refs" / f"{r['id']}.png"
            if p.exists():
                got[str(o["view"])] = p
    if len(got) < 2:
        print(f"need both sides; found {sorted(got)}")
        return 2

    left = got["left"].read_bytes()
    with Image.open(got["right"]) as im:          # mirror so both face one way
        buf = io.BytesIO()
        im.transpose(Image.FLIP_LEFT_RIGHT).save(buf, "PNG")
        right = buf.getvalue()

    import asyncio

    from backend.api.vnccs_native import _ollama_cfg
    from backend.database.database import async_session

    async def _cfg():
        async with async_session() as s:
            return await _ollama_cfg(s)

    urls, _t, vision = asyncio.run(_cfg())
    if not urls or not vision:
        print("no Ollama vision model configured")
        return 3

    L = _scan(_wiz, urls, vision, left)
    R = _scan(_wiz, urls, vision, right)
    if not L or not R:
        print("a side did not scan; L=%d R=%d slots" % (len(L), len(R)))
        return 4

    only_l = sorted(set(L) - set(R))
    only_r = sorted(set(R) - set(L))
    differing = []
    for k in sorted(set(L) & set(R)):
        wl, wr = _words(L[k]), _words(R[k])
        union = wl | wr
        if not union:
            continue
        jac = len(wl & wr) / len(union)
        if jac < 0.6:                    # same slot, materially different words
            differing.append((k, L[k], R[k], round(jac, 2)))

    score = len(only_l) + len(only_r) + len(differing)
    label = f"{args.char} / {args.outfit}" + (f" / {args.variant}" if args.variant else "")
    print(label)
    print(f"  slots seen: left {len(L)}, right {len(R)}")
    print(f"  MISMATCH SCORE: {score}   (0 = the sides agree)")
    for k in only_l:
        print(f"    only on the LEFT   {k}: {L[k]}")
    for k in only_r:
        print(f"    only on the RIGHT  {k}: {R[k]}")
    for k, a, b, j in differing:
        print(f"    differs  {k} (overlap {j}):\n        L: {a}\n        R: {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
