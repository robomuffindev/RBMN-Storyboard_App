"""v1.217 — VERSION, pyproject, CHANGELOG, docs for dressed bases."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.216.0", v.read_text("utf-8")
v.write_text("1.217.0\n", "utf-8")

pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.216.0"') == 1
pp.write_text(s.replace('version = "1.216.0"', 'version = "1.217.0"', 1), "utf-8")

ENTRY = '''## v1.217.0 -- strip is a CHOICE, not a stage (2026-08-04)

"Use our reference base and not a stripped version... in a lot of cases we can just use our
references we uploaded and the generated missing angles and use that instead of stripping the
character every time."

Right, and stripping every time costs twice: an extra Klein edit per view, AND the drift that
edit introduces. When a shot never needed the clothing replaced, that drift bought nothing --
the uploaded reference IS the better identity image.

**Two real bugs found on the way in, both squarely in the path of this feature.** Neither was
visible from reading the strip flow; both turned up while tracing what `_base_for_view` could
actually select.

1. **`ref_copy` base versions never recorded a view.** `_base_for_view` filters versions on
   `(v.get("view") or "") == view`, and the ref-copy record only ever wrote
   `{id, kind, source_ref, created_at}`. So a reference copied into the base set could NEVER be
   matched to an angle -- it was reachable only as the active base. That is precisely the "use
   my uploaded reference instead of a stripped one" path, and it has never worked per-view.
   Now records the source ref's tag.
2. **`upscaled` versions lost their provenance.** The record kept the view but not what it was
   upscaled FROM -- and `_base_for_view` prefers upscaled first, so a dressed run would have
   happily picked an upscale of a stripped image. Now records `from_kind` and `from_id`.

**The mode.** `auto` (pre-v1.217 behaviour: newest of that view wins) | `dressed` | `stripped`.
Set per character (`PUT /characters/{slug}/base-mode`) and overridable per request on
`/generate`, `/generate-set` and per LoRA dataset.

- **dressed** skips stripped versions and upscales of them, then falls through to the tagged
  reference tier -- which is inherently clothed, and is also where generated missing views land
  (`views_generate` writes them as refs with `source: "generated"`). So **a character with no
  dressed base still works entirely off his uploads and generated views, with no strip run at
  all.** That is the case he described.
- **stripped** prefers stripped versions; when a view has none it falls back to the dressed
  reference and LABELS it `dressed fallback` rather than implying a strip happened.
- Provenance that is genuinely unknown (a pre-v1.217 upscale) is **used, not dropped**, and
  labelled `provenance unknown`. Dropping it would repeat the v1.205 `ups or vers` bug, where an
  empty preferred tier skipped every candidate behind it. Guessing it would be worse.

Every return still LABELS which source won -- `front base (ref_copy)`, `back reference
(generated)`, `left reference · dressed fallback` -- so the job line, the gallery and the log
say what actually ran. `PUT /base-mode` returns `resolves_to` for all four views, so the
consequence of the toggle is visible **before** spending a render.

LoRA datasets carry `base_mode` too, and **QC now compares against the same identity source the
renders used** -- otherwise a dressed dataset checked against a stripped reference would flag
his real clothes as "not him" on every image.

Verified: `test_v1217.py` builds a character folder on disk (ref copies, strips, references, a
generated back view, a version whose file is missing, a legacy upscale) and asks the picker what
it would choose per mode -- 40 checks, all pass. Both bugs were invisible to a source grep, which
is why this exercises the picker rather than the text. All 11 suites pass on the live files
(klein2 v1204/1206/1207/1208, cross v1205/v1217, lora v1209/1210/1213/1214/1216);
klein3 md5 82a88be0c98ac7d663f628a047b6ced1, lora md5 3824884309d18780639b219012bebbb3.
`test_v1205` needed its extraction set widened -- `_base_for_view` now calls `_base_mode`.

**Not yet built: the UI.** The Klein 3.0 toggle and the LoRA dataset selector are next, together
with the v1.216 wardrobe editor. Until then the mode is reachable via the API and the character
default.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.216.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")

d = ROOT / "docs" / "KLEIN3.md"
s = d.read_text("utf-8")
DOC = '''
## Base mode — dressed vs stripped (v1.217)

Stripping is a **choice**, not a stage. It costs an extra Klein edit per view and
introduces its own drift, so when a shot never needed the clothing replaced, the
uploaded reference is the better identity image.

| mode | what it picks |
|---|---|
| `auto` | pre-v1.217 behaviour — newest base version of that view wins |
| `dressed` | clothed sources only: ref copies, upscales of them, then tagged references |
| `stripped` | stripped versions and upscales of them, falling back to a reference |

**A character with no dressed base still works in `dressed` mode.** The
tagged-reference tier is inherently clothed, and generated missing views land
there too (`views_generate` writes them as refs with `source: "generated"`), so
uploads + generated angles are enough on their own — no strip run at all.

```
PUT /characters/{slug}/base-mode   {"mode": "dressed"}
  -> {"mode": "dressed",
      "resolves_to": {"front": {"found": true, "source": "front base (ref_copy)"},
                      "back":  {"found": true, "source": "back reference (generated)"}, ...}}
```

`resolves_to` is the point of the toggle: see the consequence for every view
**before** spending a render. `/generate`, `/generate-set` and each LoRA dataset
take a `base_mode` that overrides the character default for that run.

### Provenance

Every base version now records where it came from:

- `ref_copy` → `{kind, source_ref, view}` — **the `view` is new in v1.217.** It was
  never recorded, and `_base_for_view` filters on it, so a ref copy could never be
  matched to an angle. It was reachable only as the active base.
- `upscaled` → `{kind, view, from_kind, from_id}` — **`from_kind`/`from_id` are new.**
  Without them an upscale could not be told apart from an upscale of a strip, and
  the picker prefers upscaled first.

A version with genuinely unknown provenance (an upscale written before v1.217) is
**used, not dropped**, and labelled `provenance unknown`. Dropping it would repeat
the v1.205 bug where an empty preferred tier skipped everything behind it.

### Labels

Every pick reports its source, and the label is the record of what ran — never
infer it from the code path:

```
front base (ref_copy)                back reference (generated)
front base (stripped_underwear)      left reference · dressed fallback
front base (upscaled · provenance unknown)
```

`dressed fallback` means a `stripped` run found no stripped version for that view
and used the clothed reference — it says so rather than implying a strip happened.
'''
assert "## Base mode — dressed vs stripped" not in s
d.write_text(s.rstrip() + "\n" + DOC, "utf-8")
print("VERSION 1.217.0 · pyproject · CHANGELOG · docs/KLEIN3.md")
