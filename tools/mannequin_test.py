#!/usr/bin/env python3
"""Render the VNCCS parametric mannequin as a FAT + SHORT figure via MakeHuman
morphs (weight/height), to validate replacing the scanned clay.

pose_render already runs a MakeHuman solver (`_solve_base_verts`) that takes
weight/height/gender/muscle factors -> we just weren't setting them to match
Duke. This renders a grid of weight/height settings + one arms-out pose (to
confirm the parametric arms deform cleanly, no rig, no smear).

Run:  venv\\Scripts\\python.exe tools\\mannequin_test.py  (app venv, has numpy +
CharacterData). Output -> <repo>/_diag/mannequin_test/.
"""
from __future__ import annotations
import base64, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "mannequin_test"
sys.path.insert(0, str(REPO))


def save_caps(caps, prefix):
    n = 0
    for i, c in enumerate(caps or []):
        if not isinstance(c, str) or "," not in c:
            continue
        raw = base64.b64decode(c.split(",", 1)[1])
        (OUT / f"{prefix}_{i:02d}.png").write_bytes(raw)
        n += 1
    return n


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    from backend.services.character_studio.vnccs_native import pose_render
    if not pose_render._ensure_loaded():
        print("ERROR: pose_render could not load MakeHuman/CharacterData")
        return
    # dump skeleton bone names so we can verify the belly targeting
    try:
        bn = [b.name for b in pose_render._STATE["skeleton"].boneslist]
        (OUT / "bones.txt").write_text("\n".join(bn), encoding="utf-8")
        print("bones:", ", ".join(bn))
    except Exception as e:
        print("bone dump failed:", e)
    export = {"view_width": 1024, "view_height": 1216, "bg_color": [40, 40, 40]}
    stand = {"bones": {}, "modelRotation": [0, 0, 0]}
    arms_out = {"bones": {"upperarm_l": [0, 0, -60], "lowerarm_l": [0, -40, 0],
                          "upperarm_r": [0, 0, 60], "lowerarm_r": [0, 40, 0]},
                "modelRotation": [0, 0, 0]}
    # (label, weight, height) — gender=male, muscle mid, adult
    # weight 1.0 + short, sweeping the PROCEDURAL belly to see how big a gut we can get
    grid = [
        ("belly00", 1.0, 0.2, 0.0),
        ("belly06", 1.0, 0.2, 0.6),
        ("belly10", 1.0, 0.2, 1.0),
        ("belly15", 1.0, 0.2, 1.5),
    ]
    for label, w, h, belly in grid:
        mesh = {"gender": 1.0, "weight": w, "height": h, "muscle": 0.5, "age": 32, "belly": belly}
        caps = pose_render.render_pose_captures(
            {"poses": [stand], "export": export, "mesh": mesh}, False)
        got = save_caps(caps, label)
        print(f"{label}: weight={w} height={h} belly={belly} -> {got} img")
    # arms-out at big belly to confirm clean parametric arms + belly together
    mesh = {"gender": 1.0, "weight": 1.0, "height": 0.20, "muscle": 0.5, "age": 32, "belly": 1.0}
    caps = pose_render.render_pose_captures(
        {"poses": [arms_out], "export": export, "mesh": mesh}, False)
    print("arms_out_belly10:", save_caps(caps, "arms_out_belly10"), "img")
    print("wrote ->", OUT)
    print("Tell Claude: mannequin done")


if __name__ == "__main__":
    main()
