"""v1.209.0 — wire the 🎓 LoRA Dataset mode in (main.py + VNCCSNativePage.tsx).

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_wire_lora_v1209.py <repo-root>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])


def rep(rel: str, old: str, new: str, label: str) -> None:
    p = root / rel
    s = p.read_text("utf-8")
    n = s.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    p.write_text(s.replace(old, new), "utf-8")
    print(f"  ok  {label}")


# ── backend router ────────────────────────────────────────────────────────
rep("backend/main.py",
    """from backend.api.klein3 import router as klein3_router  # noqa: E402
app.include_router(klein3_router)""",
    """from backend.api.klein3 import router as klein3_router  # noqa: E402
app.include_router(klein3_router)

from backend.api.lora import router as lora_router  # noqa: E402
app.include_router(lora_router)""",
    "main.py router")

# ── frontend: import, Tab type, mode button, render branch ───────────────
rep("frontend/src/components/VNCCSNative/VNCCSNativePage.tsx",
    """type Tab = 'create' | 'clothes' | 'emotions' | 'cloner' | 'poselib';""",
    """type Tab = 'create' | 'clothes' | 'emotions' | 'cloner' | 'poselib' | 'lora';""",
    "Tab type")

rep("frontend/src/components/VNCCSNative/VNCCSNativePage.tsx",
    """        <button style={tabBtn(tab === 'poselib')} onClick={() => setTab('poselib')}>Pose Library</button>
      </div>""",
    """        <button style={tabBtn(tab === 'poselib')} onClick={() => setTab('poselib')}>Pose Library</button>
        <button style={tabBtn(tab === 'lora')} onClick={() => setTab('lora')}>🎓 LoRA Dataset Gen</button>
      </div>""",
    "mode button")

rep("frontend/src/components/VNCCSNative/VNCCSNativePage.tsx",
    """      ) : tab === 'poselib' ? (""",
    """      ) : tab === 'lora' ? (
        <LoraPanel />
      ) : tab === 'poselib' ? (""",
    "render branch")

print("  ..  adding the LoraPanel import")
p = root / "frontend/src/components/VNCCSNative/VNCCSNativePage.tsx"
s = p.read_text("utf-8")
anchor = "import Klein3Panel from './Klein3Panel';"
if anchor in s:
    assert s.count(anchor) == 1
    s = s.replace(anchor, anchor + "\nimport LoraPanel from './LoraPanel';")
else:                       # fall back to the last local import line
    import re as _re
    m = list(_re.finditer(r"^import .*from '\./[^']+';$", s, _re.M))
    assert m, "no local import line found"
    at = m[-1].end()
    s = s[:at] + "\nimport LoraPanel from './LoraPanel';" + s[at:]
assert s.count("import LoraPanel from './LoraPanel';") == 1
p.write_text(s, "utf-8")
print("  ok  LoraPanel import")
print("wired.")
