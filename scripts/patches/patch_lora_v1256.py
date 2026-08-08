"""v1.256 — my own escaping bug, inside the fix for an escaping bug.

v1.255 wrote this into the generated `fizgig_run.py`:

    resolved.write_text("\\n".join(lines) + "\\n", "utf-8")

The runner is a plain triple-quoted template inside `lora.py`, so escape
sequences are processed when the BACKEND IMPORTS lora.py, not when the script is
written to disk. Those two characters became an actual newline, and the
generated script died on its first line of real work:

    File "...\\fizgig_run.py", line 105
        resolved.write_text("
                            ^
    SyntaxError: unterminated string literal

The template contains no other escape sequences, which is why nothing had hit
this before — and why the right fix is not to escape harder but to stop needing
an escape at all. Writing the file line by line through `print(..., file=fh)`
has no string literal to get wrong.

A generated file is source code twice over. Every backslash in this template is
a trap, and out-escaping it would have left something nobody can safely edit.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src

# The FILE holds a literal backslash-n, so only a raw string matches it.
BAD = r'''    resolved = HERE / "dataset_fizgig.resolved.toml"
    resolved.write_text("\n".join(lines) + "\n", "utf-8")
    cfgp = str(resolved)'''

GOOD = '''    resolved = HERE / "dataset_fizgig.resolved.toml"
    # Written line by line on purpose: this script is a TEMPLATE inside
    # lora.py, so any escape sequence here is processed when the backend
    # imports it rather than when the file is written. v1.255 lost a newline
    # escape exactly that way and generated an unterminated string literal.
    with resolved.open("w", encoding="utf-8") as _fh:
        for _ln in lines:
            print(_ln, file=_fh)
    cfgp = str(resolved)'''

n = src.count(BAD)
assert n == 1, f"expected 1 broken block, found {n}"
src = src.replace(BAD, GOOD, 1)

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
