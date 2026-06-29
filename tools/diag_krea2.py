#!/usr/bin/env python3
"""diag_krea2.py — Why is the first pass still Z-Image when Krea 2 is selected?

Checks the THREE things the dispatcher's first-pass redirect requires to choose
Krea 2 for a no-reference / two-pass-base render:
  1. app_settings.single_image_generator == 'krea2_turbo'   (the saved setting)
  2. KREA2_TURBO_T2I.json present in the workflows/ folder
  3. the app VERSION on disk (so you can confirm the running backend was restarted)

Writes ./krea2_diag.md (also prints). Stdlib only.

Usage:
    python tools/diag_krea2.py
    python tools/diag_krea2.py --db PATH
"""
from __future__ import annotations
import argparse, sqlite3, sys
from pathlib import Path


def find_db(explicit):
    cands = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    cands.append(Path("~/RBMN-Projects/RBMN.db").expanduser())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.config import settings as _s
        cands.append(Path(str(_s.db_path)))
    except Exception:
        pass
    for c in cands:
        if c.exists():
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default="krea2_diag.md")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    L = ["# Krea 2 First-Pass Diagnostic", ""]

    # 3) version on disk
    ver = (repo / "VERSION")
    ver_txt = ver.read_text(encoding="utf-8").strip() if ver.exists() else "?"
    L.append(f"- VERSION on disk: **{ver_txt}**  (Krea 2 routing needs >= 1.9.0; "
             f"the RUNNING backend must have been restarted on this version)")

    # 2) workflow file presence
    wf = repo / "workflows" / "KREA2_TURBO_T2I.json"
    L.append(f"- workflows/KREA2_TURBO_T2I.json present: **{wf.exists()}**  (`{wf}`)")

    # 1) the saved setting
    db = find_db(args.db)
    if not db:
        L.append("- ERROR: could not locate RBMN.db — pass --db PATH")
    else:
        L.append(f"- DB: `{db}`")
        try:
            con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT single_image_generator, krea2_model_name, project_dir "
                "FROM app_settings WHERE id = 1"
            ).fetchone()
            if row is None:
                row = con.execute(
                    "SELECT single_image_generator, krea2_model_name, project_dir "
                    "FROM app_settings LIMIT 1"
                ).fetchone()
            if row is None:
                L.append("- ERROR: no app_settings row found")
            else:
                sig = row["single_image_generator"]
                L.append(f"- app_settings.single_image_generator = **{sig!r}**")
                try:
                    L.append(f"- app_settings.krea2_model_name = **{row['krea2_model_name']!r}**")
                except Exception:
                    L.append("- app_settings.krea2_model_name = (column missing — DB not migrated; restart backend on >=1.9.0)")
        except Exception as e:
            L.append(f"- ERROR reading app_settings: {e}")

    # verdict
    L.append("")
    L.append("## Verdict")
    sig_ok = False
    try:
        sig_ok = (sig == "krea2_turbo")
    except Exception:
        pass
    if not wf.exists():
        L.append("❌ The workflow file is missing from `workflows/` — the redirect "
                 "falls back to Z-Image. Add KREA2_TURBO_T2I.json and restart.")
    elif not sig_ok:
        L.append("❌ single_image_generator is NOT 'krea2_turbo' — your selection "
                 "didn't persist. Open Settings, pick **Krea 2 Turbo** under Single "
                 "Image Generator, Save, then re-check. (If it reverts, the save is "
                 "failing — tell Claude.)")
    else:
        L.append("✅ Setting and workflow file are both correct. If the first pass "
                 "STILL renders as Z-Image, the running backend is on OLD code — "
                 "fully stop and restart the app (not just the browser) so the new "
                 "Krea 2 redirect loads. Confirm /openapi.json reports "
                 f"version {ver_txt}.")

    out = Path(args.out)
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n[wrote {out.resolve()}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
