#!/usr/bin/env python3
"""Validate the 3D-scan -> mannequin body fit (mesh_fit) for a character.

Loads <project>/mesh3d/<id>/character.glb (same resolution as the app: DB
app_settings.project_dir override, else ~/RBMN-Projects), measures the scan's
torso depth profile, fits mannequin weight/belly, and renders the FITTED
mannequin (front/side) next to the DESCRIPTION-derived one for comparison.
Output -> <repo>/_diag/meshfit_test/.  Run via meshfit_test.bat [character]
(app venv).  Optional: --glb <path> to test an explicit .glb file.
"""
from __future__ import annotations
import base64, io, json, os, sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "meshfit_test"
sys.path.insert(0, str(REPO))

args = list(sys.argv[1:])
glb_override = None
if "--glb" in args:
    i = args.index("--glb")
    glb_override = args[i + 1]
    del args[i:i + 2]
CHAR = args[0].strip() if args and args[0].strip() else "Duke"


def project_dir() -> Path:
    env = os.environ.get("PROJECT_DIR")
    dflt = Path(os.path.expanduser(env)) if env else Path(os.path.expanduser("~/RBMN-Projects"))
    db = dflt / "RBMN.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                return Path(os.path.expanduser(row[0]))
        except Exception as e:  # noqa: BLE001
            print("WARN app_settings:", e)
    return dflt


def find_glb(char: str):
    root = project_dir() / "mesh3d"
    if not root.is_dir():
        print("WARN: no mesh3d dir at", root)
        return None
    named, unnamed = [], []
    for d in root.iterdir():
        glb = d / "character.glb"
        if not glb.exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        nm = str(meta.get("character_name") or "").strip()
        if nm == char:
            named.append(glb)
        elif not nm:
            unnamed.append(glb)
    return named[0] if named else (unnamed[0] if len(unnamed) == 1 else None)


def manifest_info(char: str) -> dict:
    p = REPO / "_diag" / f"manifest_{char}.json"
    try:
        mj = json.loads(p.read_text(encoding="utf-8"))
        return ((mj.get("vnccs") or {}).get("form") or {}).get("character_info") or {}
    except Exception:  # noqa: BLE001
        return {}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    from backend.services.character_studio.vnccs_native import pose_render, mesh_fit, klein_poses
    if not pose_render._ensure_loaded():
        print("ERROR: CharacterData not loadable")
        return
    glb = Path(glb_override) if glb_override else find_glb(CHAR)
    if not glb or not glb.exists():
        print("ERROR: no character.glb found for", CHAR)
        return
    print("glb:", glb)
    v = mesh_fit.load_glb_positions(glb)
    print("verts:", None if v is None else len(v))
    prof = mesh_fit.depth_profile(v) if v is not None else None
    print("scan depth profile:", prof)
    if not prof:
        return
    ci = manifest_info(CHAR)
    if ci:
        text_mesh = dict(klein_poses.body_mesh_params(ci))
        print("description-derived mesh:", text_mesh)
    else:
        text_mesh = {"gender": 1.0, "weight": 0.5, "muscle": 0.5, "height": 0.5, "age": 40}
        print("no manifest in _diag (run rbmn_diag.bat to dump) -- using neutral base")
    fitted = mesh_fit.fit_weight_belly(prof, text_mesh)
    print("FITTED from 3D scan:", fitted)
    if not fitted:
        return
    fit_mesh = {**text_mesh, **fitted}
    from PIL import Image
    export = {"view_width": 480, "view_height": 600, "bg_color": [40, 40, 40]}

    def r(mesh, rot):
        caps = pose_render.render_pose_captures(
            {"poses": [{"bones": {}, "modelRotation": rot}], "export": export,
             "mesh": mesh}, False)
        return Image.open(io.BytesIO(base64.b64decode(caps[0].split(",", 1)[1])))

    tiles = [r(text_mesh, [0, 0, 0]), r(text_mesh, [0, 90, 0]),
             r(fit_mesh, [0, 0, 0]), r(fit_mesh, [0, 90, 0])]
    w, h = tiles[0].size
    grid = Image.new("RGB", (w * 4, h))
    for i, im in enumerate(tiles):
        grid.paste(im, (i * w, 0))
    grid.save(OUT / f"{CHAR}_text_vs_fit.png")
    (OUT / f"{CHAR}_report.json").write_text(
        json.dumps({"glb": str(glb), "profile": prof, "text_mesh": text_mesh,
                    "fitted": fitted}, indent=1), encoding="utf-8")
    print("wrote ->", OUT)
    print("Tell Claude: meshfit done")


if __name__ == "__main__":
    main()
