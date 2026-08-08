#!/usr/bin/env python3
"""Score EVERY library pose for arm-inside-torso penetration, for one character.

WHY: the arm-burial failure depends on the pose, and the pose library was authored
for average builds. Rather than argue about which poses are safe, measure all of
them for THIS body and rank. That turns "curate the pose set" from an opinion into
a sorted list, and it is the same measurement the runtime filter will use.

Also fixes a methodology error: depth_test had been grading against whatever single
pose happened to be in the newest last_pose_run dump -- a sample of one, and a
rear-facing one at that.

Run:  pose_audit.bat [character]      (default Duke)
Out:  <repo>/_diag/pose_audit/  -> pose_NN.png, contact_sheet.png, report.json
Then tell Claude: "audit done".
"""
from __future__ import annotations
import json, os, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "pose_audit"
sys.path.insert(0, str(REPO))
CHAR = next((a for a in sys.argv[1:] if not a.startswith("-")), "Duke")
W, H = 512, 768          # small: this is a screening pass over many poses


def data_dir() -> Path:
    home = Path(os.path.expanduser(os.environ.get("PROJECT_DIR") or "~/RBMN-Projects"))
    for db in (home / "RBMN.db",):
        if db.exists():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
                con.close()
                if row and row[0]:
                    return Path(os.path.expanduser(str(row[0])))
            except Exception:  # noqa: BLE001
                pass
    return home


def find_rigged_fbx(character: str):
    root = data_dir() / "mesh3d"
    if not root.is_dir():
        return None
    named, unnamed = [], []
    for d in sorted(root.iterdir()):
        fbx = d / "rigged.fbx"
        if not fbx.exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        nm = str(meta.get("character_name") or "").strip()
        if nm.lower() == character.lower():
            named.append(fbx)
        elif not nm:
            unnamed.append(fbx)
    return named[0] if named else (unnamed[0] if len(unnamed) == 1 else None)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    from backend.services.character_studio.vnccs_native import mia_rig, workflows

    poses = list(workflows.creator_baseline_pose_data().get("poses") or [])
    if not poses:
        print("ERROR: no baseline poses found")
        return 1
    poses = poses[:16]                       # clay_driver's per-run cap
    fbx = find_rigged_fbx(CHAR)
    print(f"pose_audit: {CHAR}  poses={len(poses)}  fbx={fbx}")
    if not fbx:
        print(f"ERROR: no rigged.fbx for {CHAR} under {data_dir()}")
        return 1

    report = {"character": CHAR, "n_poses": len(poses), "fbx": str(fbx)}
    rows = []
    # v1.199.110: three-way. Penetration alone is NOT sufficient -- poses #3 and
    # #5 scored 31-32% yet render visibly smeared, because arm burial and armpit
    # SMEAR are two independent failure modes. maxstretch per pose captures the
    # second one, and the Blender re-skin (measured 17x lower peak stretch at v99,
    # parked at v103 only to cut variables) is the candidate fix for it.
    # v1.199.111: the "abduct_reskin" pass is REMOVED. Blender's ARMATURE_AUTO
    # heat weights destroy this mesh -- the contact sheet showed 12 unrecognisable
    # blobs, no human form at all. Its 10-15x lower maxstretch (31.4 -> 2.1 etc.)
    # was the mesh COLLAPSING uniformly rather than deforming correctly, which
    # also explains torso_verts=0: the weights are garbage. maxstretch rewards
    # collapse, so a low value is necessary but nowhere near sufficient.
    for mode, tag, reskin in (("on", "abduct", "off"),
                              ("off", "plain", "off"),
                              ("on", "abduct_reskin", "blender")):
        with tempfile.TemporaryDirectory(prefix="paudit_") as td:
            out_dir = Path(td) / "out"
            job = {"fbx": str(fbx), "width": W, "height": H, "out_dir": str(out_dir),
                   "render_mode": "depth", "smear_stretch": 1e6, "audit": True,
                   "auto_abduct": (mode == "on"), "reskin": reskin,
                   "poses": [{"bones": (p or {}).get("bones") or {},
                              "modelRotation": (p or {}).get("modelRotation") or [0, 0, 0]}
                             for p in poses]}
            jp = Path(td) / "job.json"
            jp.write_text(json.dumps(job), encoding="utf-8")
            r = subprocess.run([str(mia_rig._venv_python()),
                                str(mia_rig.MIA_LOCAL_DIR / "clay_driver.py"), "--job", str(jp)],
                               capture_output=True, text=True, timeout=3600,
                               cwd=str(mia_rig.MIA_LOCAL_DIR))
            pen = [float(m) for m in re.findall(r"CLAY_PENETRATION .*?\(([\d.]+)%\)", r.stdout or "")]
            pen = pen[:len(poses)]           # first pass = one reading per pose, in order
            # maxstretch is printed once per pose in the RENDER pass, in order
            stretch = [float(m) for m in
                       re.findall(r"CLAY_POSE \d+/\d+ dropped=\d+ maxstretch=([\d.]+)", r.stdout or "")]
            report[f"{tag}_rc"] = r.returncode
            report[f"{tag}_penetration"] = pen
            report[f"{tag}_maxstretch"] = stretch[:len(poses)]
            # keep the clay log: diagnosing by eye off a contact sheet is how the
            # re-skin got written off for the wrong reason
            report[f"{tag}_log"] = [ln for ln in (r.stdout or "").splitlines()
                                    if ln.startswith("CLAY_LOG")][:14]
            if not pen:
                report[f"{tag}_stderr"] = (r.stderr or "").strip().splitlines()[-8:]
            if tag == "abduct_reskin":
                for i in range(len(poses)):
                    src = out_dir / f"pose_{i:03d}.png"
                    if src.exists():
                        from PIL import Image  # noqa: PLC0415
                        from backend.services.character_studio.vnccs_native import pose_clay as _pc
                        _pc._renorm_depth(Image.open(src)).save(OUT / f"pose_{i:02d}.png")

    on = report.get("abduct_penetration") or []
    off = report.get("plain_penetration") or []
    rk = report.get("abduct_reskin_penetration") or []
    sk = report.get("abduct_reskin_maxstretch") or []
    sa = report.get("abduct_maxstretch") or []
    def _g(l, i):
        return l[i] if i < len(l) else None
    for i in range(len(poses)):
        rows.append({"pose": i,
                     "pen_plain": _g(off, i), "pen_abduct": _g(on, i),
                     "pen_abduct_reskin": _g(rk, i),
                     "stretch_abduct": _g(sa, i), "stretch_abduct_reskin": _g(sk, i),
                     "modelRotation": (poses[i] or {}).get("modelRotation")})
    # a pose is usable only if BOTH failure modes are low
    best = [r for r in rows
            if (r["pen_abduct_reskin"] if r["pen_abduct_reskin"] is not None
                else r["pen_abduct"] or 100) <= 35
            and (r["stretch_abduct_reskin"] if r["stretch_abduct_reskin"] is not None
                 else r["stretch_abduct"] or 1e9) <= 30]
    report["rows"] = rows
    report["recommended_poses"] = [r["pose"] for r in best]

    # contact sheet so the ranking can be eyeballed, not just read
    try:
        from PIL import Image, ImageDraw
        tiles = []
        for i in range(len(poses)):
            f = OUT / f"pose_{i:02d}.png"
            if f.exists():
                tiles.append((i, Image.open(f).convert("RGB")))
        if tiles:
            tw, th = 200, 300
            cols = 6
            rowsn = (len(tiles) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * tw, rowsn * (th + 22)), (18, 20, 26))
            d = ImageDraw.Draw(sheet)
            for k, (i, im) in enumerate(tiles):
                x = (k % cols) * tw
                y = (k // cols) * (th + 22)
                sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y + 22))
                p = _g(rk, i)
                p = p if p is not None else (_g(on, i) or -1)
                st = _g(sk, i)
                st = st if st is not None else (_g(sa, i) or -1)
                ok = 0 <= p <= 35 and 0 <= st <= 30
                col = (126, 224, 176) if ok else (255, 160, 120)
                d.text((x + 4, y + 5), f"#{i}  pen {p:.0f}%  str {st:.0f}", fill=col)
            sheet.save(OUT / "contact_sheet.png")
            report["contact_sheet"] = str(OUT / "contact_sheet.png")
    except Exception as e:  # noqa: BLE001
        report["contact_sheet_error"] = str(e)

    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n  pose  plain%  abduct%  +reskin%   stretch(abd)  stretch(+reskin)")
    for r in rows:
        print(f"   {r['pose']:>3}  {str(r['pen_plain']):>6}  {str(r['pen_abduct']):>7}  "
              f"{str(r['pen_abduct_reskin']):>8}   {str(r['stretch_abduct']):>11}  "
              f"{str(r['stretch_abduct_reskin']):>15}")
    print(f"\nrecommended (<=35% with abduction): {report['recommended_poses']}")
    print("wrote ->", OUT)
    print('Tell Claude: "audit done"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
