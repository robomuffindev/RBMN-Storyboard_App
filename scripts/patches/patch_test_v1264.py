"""test_v1234: `not_checked` grew a second entry in v1.261.

The fixture has no rembg AND has never had a wardrobe pass, so the honest answer
is now BOTH. The assertion was written when crop was the only unchecked
instrument; it is updated, not relaxed — it still demands the exact set.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/patches/test_v1234.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''    check("crop reports UNCHECKED when there is no usable mask",
          f["not_checked"] == ["crop"], str(f["not_checked"]))''',
    '''    # v1.261 added the wardrobe check, and this fixture has never run one, so
    # the honest answer is both. Still an exact-set assertion.
    check("crop AND wardrobe report UNCHECKED when neither has run",
          f["not_checked"] == ["crop", "wardrobe"], str(f["not_checked"]))
    check("nothing in this fixture was measured for wardrobe",
          (f["wardrobe_measured"], f["bare_skin"]) == (0, 0),
          f"{f['wardrobe_measured']}/{f['bare_skin']}")''',
    "not_checked")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
