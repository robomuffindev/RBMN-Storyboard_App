"""v1.225 — VERSION, pyproject, CHANGELOG for the UI."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.224.0", v.read_text("utf-8")
v.write_text("1.225.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.224.0"') == 1
pp.write_text(s.replace('version = "1.224.0"', 'version = "1.225.0"', 1), "utf-8")
ENTRY = '''## v1.225.0 -- the UI for v1.216 and v1.217 (2026-08-05)

Both shipped backend-only and I buried that at the bottom of long messages, so he went looking
in Klein 3.0 for the clothing option and found nothing. This is the part that was missing.

**Klein3Panel -- identity source.** A three-way toggle (dressed / stripped / auto) directly above
the Strip card, with the per-view resolution shown underneath: green where a real reference backs
that view, amber where it is falling back. `PUT /base-mode` returns `resolves_to`, so the
consequence is visible BEFORE a render is spent on it. Dressed skips the strip step entirely --
one less edit per view and one less source of drift.

**LoraPanel -- wardrobe.** Replaces the single "fixed outfit" text box that was causing the
bake-in problem in the first place:
  * named vs variety rows, with the live 60/40 split and images-per-outfit
  * "Suggest variety" reads the character's own reference and proposes NAMED garments, appended
    for review and never applied silently
  * a garment reference picker that calls the vision model to name what is in the image, because
    Klein ignores category words -- picking a reference auto-fills the description
  * the sizing checkbox, showing the sized-for-this-wardrobe count, and an amber warning below
    ~8 images per outfit with the measured reason ("some outfits only appear in 2 of the 4 shot
    types, which trains 'that outfit means that shot'")
  * a per-dataset identity-source override

Both files compile clean under esbuild in the sandbox (the device VM cannot run vite -- its
rollup binary is Windows-built).

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.224.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.225.0 · pyproject · CHANGELOG")
