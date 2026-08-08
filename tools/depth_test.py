#!/usr/bin/env python3
"""Validate the v1.199.83 DEPTH pose renders WITHOUT touching a ComfyUI worker.

Renders the same pose battery four ways and dumps pixel statistics, so we can
tell at a glance whether each renderer produced a real depth map:

  clay_shaded_*   the rigged mesh, Workbench clay (the old reference image)
  clay_depth_*    the rigged mesh, Blender Z pass -> near=white / far=black
  mann_shaded_*   the parametric mannequin, node-faithful shading
  mann_depth_*    the parametric mannequin, z-buffer depth

WHY THE STATS MATTER: if the Z pass silently fails, the image comes out flat
(one value everywhere).  ``spread`` below is the 5th..95th percentile range of
the BODY pixels -- a real depth map of a human is well above 30; anything under
~10 means the pass did not render and we should switch clay to EEVEE.

Run:  depth_test.bat [character]        (default Duke)
Out:  <repo>/_diag/depth_test/  -> PNGs + depth_report.json
Then tell Claude: "depth test done".
"""
from __future__ import annotations
import base64, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "depth_test"
sys.path.insert(0, str(REPO))
_ARGS = [a for a in sys.argv[1:] if a.strip()]
USE_LAST = any(a.lower() in ("--last", "-l") for a in _ARGS)
_POS = [a for a in _ARGS if not a.startswith("-")]
CHAR = _POS[0].strip() if _POS else "Duke"

# A standing pose, a big-arm pose (the smear regime) and a yawed pose.
BATTERY = [
    ("00_rest", {}, [0, 0, 0]),
    ("01_arms_up", {"upperarm_l": [0, 0, -120], "upperarm_r": [0, 0, 120]}, [0, 0, 0]),
    ("02_yaw45", {"upperarm_l": [0, 0, -25], "upperarm_r": [0, 0, 25]}, [0, 45, 0]),
]
W, H = 832, 1216


def load_last_poses():
    """Poses from the newest _diag/last_pose_run dump = EXACTLY what Klein saw.

    Synthetic battery poses are not representative: the library poses that fail
    carry large body yaw and folded arms, which is precisely the regime where the
    clay smear/hole behaviour differs. Reproducing the real pose is the only way
    to compare a depth ref against its own shaded ground truth.
    """
    root = REPO / "_diag" / "last_pose_run"
    if not root.is_dir():
        return None, None
    dirs = sorted([d for d in root.iterdir() if d.is_dir()])
    for d in reversed(dirs):
        try:
            pj = json.loads((d / "params.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        poses = pj.get("poses") or []
        if poses:
            out = [(f"{i:02d}_last", (p or {}).get("bones") or {},
                    (p or {}).get("modelRotation") or [0, 0, 0])
                   for i, p in enumerate(poses)]
            return out, d.name
    return None, None


def data_dir() -> Path:
    """Where mesh3d/ actually lives.

    A standalone tool does NOT get the app's startup override, and Lorenzo's
    install points Project Directory at D:\\RBMN-Projects while the DB stays on
    C:. So read app_settings.project_dir out of the DB exactly like the app does
    -- otherwise every rig lookup silently comes back empty.
    """
    home = Path(os.path.expanduser(os.environ.get("PROJECT_DIR") or "~/RBMN-Projects"))
    for db in (home / "RBMN.db", Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db"):
        if not db.exists():
            continue
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
    """Same rule as pose_clay._find_rigged_fbx, but against the REAL data dir."""
    root = data_dir() / "mesh3d"
    if not root.is_dir():
        print(f"  (no {root})")
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
        (named if nm.lower() == character.lower() else unnamed if not nm else []).append(fbx)
    if named:
        return named[0]
    if len(unnamed) == 1:
        return unnamed[0]
    return None


def stats(png: Path) -> dict:
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(png)
        if str(im.mode).startswith("I"):      # 16-bit: scale, never clip
            v = np.asarray(im).astype("float64")
            a = (v / max(v.max(), 1.0) * 255.0).astype(np.uint8)
        else:
            a = np.asarray(im.convert("L"), dtype=np.uint8)
        body = a[a > 0]
        if body.size == 0:
            return {"error": "image is entirely background"}
        lo, hi = np.percentile(body, [5, 95])
        return {"px": int(a.size), "bg_pct": round(float((a == 0).mean()) * 100, 1),
                "body_min": int(body.min()), "body_max": int(body.max()),
                "body_mean": round(float(body.mean()), 1),
                "spread": round(float(hi - lo), 1),
                "flat_warning": bool(float(hi - lo) < 10.0)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def run_clay(fbx: Path, mode: str, report: dict, preserve_volume: bool = False,
             tag: str = "", fix_orphans: bool = True,
             smear: float = 1e6, corrective: float = 1.0, weld_rel: float = 0.0005,
             reskin: str = "blender", abduct: float = 0.0, auto_abduct: bool = True) -> None:
    # v1.199.116: DEFAULTS NOW MATCH PRODUCTION (pose_clay): reskin="blender" and a
    # SCALE-RELATIVE weld. This tool was still passing reskin="off" + weld_dist=0.001
    # (the fixed weld that merges ONE vertex on a ~200-unit mesh), so every render it
    # produced used MIA's raw weights -- NOT what the app ships. Measured on Duke's
    # rig (sandbox, same clay_driver, same 4 library poses): maxstretch 390-1064 with
    # the old tool config vs 4-12.5 with the production config, and auto-abduction
    # needed 60deg-clamped corrections vs max 47.9deg. A diagnostic that does not
    # reproduce the production conversion is worse than none.
    from backend.services.character_studio.vnccs_native import mia_rig
    mia_dir = mia_rig.MIA_LOCAL_DIR
    with tempfile.TemporaryDirectory(prefix="dtest_") as td:
        out_dir = Path(td) / "out"
        job = {"fbx": str(fbx), "width": W, "height": H, "out_dir": str(out_dir),
               "render_mode": mode,
               "smear_stretch": smear if mode in ("depth", "normal") else 2.0,
               "preserve_volume": preserve_volume,
               "fix_orphan_weights": fix_orphans,
               "corrective_smooth": corrective, "audit": True, "weld_rel": weld_rel,
               "reskin": reskin, "arm_abduct_deg": abduct, "auto_abduct": auto_abduct,
               "poses": [{"bones": b, "modelRotation": r} for _, b, r in BATTERY]}
        jp = Path(td) / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        r = subprocess.run([str(mia_rig._venv_python()),
                            str(mia_dir / "clay_driver.py"), "--job", str(jp)],
                           capture_output=True, text=True, timeout=1200, cwd=str(mia_dir))
        key = f"clay_{mode}{tag}"
        report[f"{key}_rc"] = r.returncode
        report[f"{key}_log"] = [ln for ln in (r.stdout or "").splitlines()
                                if ln.startswith("CLAY_")]
        if r.returncode != 0:
            report[f"{key}_stderr"] = (r.stderr or "").strip().splitlines()[-12:]
        for i, (label, _b, _r) in enumerate(BATTERY):
            src = out_dir / f"pose_{i:03d}.png"
            if not src.exists():
                continue
            dst = OUT / f"{key}_{label}.png"
            if mode == "depth":
                # v1.199.94: clay_driver writes depth as a 16-bit Z render; the
                # 8-bit map Klein actually receives is produced by
                # pose_clay._renorm_depth. Copying the raw file and reading it
                # with .convert("L") CLIPS every value above 255, which reported
                # a pure-white silhouette (spread 0.0) and looked exactly like a
                # catastrophic regression. Run the real conversion so this tool
                # shows the same pixels the model sees.
                from PIL import Image
                from backend.services.character_studio.vnccs_native import pose_clay as _pc
                _pc._renorm_depth(Image.open(src)).save(dst, format="PNG")
            elif mode == "normal":
                # v1.199.115: replicate pose_clay's normal composite (flat
                # facing-camera 128,128,255 backdrop) so the PNG here is the
                # same image Klein would receive.
                from PIL import Image
                _img = Image.open(src).convert("RGBA")
                _base = Image.new("RGBA", _img.size, (128, 128, 255, 255))
                _base.alpha_composite(_img)
                _base.convert("RGB").save(dst, format="PNG")
            else:
                dst.write_bytes(src.read_bytes())
            if mode == "normal":
                report.setdefault(f"{key}_stats", {})[label] = stats_normal(dst)
            else:
                report.setdefault(f"{key}_stats", {})[label] = stats(dst)


def stats_normal(png: Path) -> dict:
    """Sanity stats for a NORMAL map: the background is (128,128,255), so the
    body mask is 'differs from bg'. coverage < 2% means the matcap render
    failed (blank frame); channel_spread near 0 means the matcap did not
    actually encode direction (flat colour body = broken studio light)."""
    try:
        from PIL import Image
        import numpy as np
        a = np.asarray(Image.open(png).convert("RGB")).astype("int16")
        bg = np.array([128, 128, 255], dtype="int16")
        body = np.abs(a - bg).sum(axis=2) > 24
        cov = float(body.mean()) * 100.0
        out = {"px": int(a.shape[0] * a.shape[1]), "coverage_pct": round(cov, 1)}
        if body.sum() > 64:
            for ci, cn in enumerate("RGB"):
                ch = a[..., ci][body]
                lo, hi = np.percentile(ch, [5, 95])
                out[f"{cn}_spread"] = round(float(hi - lo), 1)
            out["flat_warning"] = bool(max(out.get("R_spread", 0),
                                           out.get("G_spread", 0)) < 15.0)
        else:
            out["error"] = "no body pixels -- matcap render failed?"
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def run_mannequin(mode: str, report: dict) -> None:
    from backend.services.character_studio.vnccs_native import pose_render
    from backend.services.character_studio.vnccs_native import workflows as _wf
    try:
        blob = _wf.creator_baseline_pose_data()
    except Exception:  # noqa: BLE001
        blob = {}
    mesh = {**(blob.get("mesh") or {}), "gender": 1.0, "age": 45.0,
            "weight": 1.0, "belly": 1.5, "height": 0.278}
    export = {**(blob.get("export") or {}), "view_width": W, "view_height": H,
              "node_camera": True, "render_mode": mode}
    poses = [{"bones": b, "modelRotation": r} for _, b, r in BATTERY]
    caps = pose_render.render_pose_captures({"mesh": mesh, "export": export,
                                             "poses": poses}, False)
    report[f"mann_{mode}_ok"] = bool(caps)
    for i, (label, _b, _r) in enumerate(BATTERY):
        if not caps or i >= len(caps):
            continue
        dst = OUT / f"mann_{mode}_{label}.png"
        dst.write_bytes(base64.b64decode(caps[i].split(",", 1)[1]))
        report.setdefault(f"mann_{mode}_stats", {})[label] = stats(dst)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    report: dict = {"character": CHAR}
    global BATTERY
    # v1.199.107: prefer the REAL poses by default. The synthetic battery's
    # "arms straight up at 120deg" is not in any library and is not what fails
    # for Lorenzo -- optimising against it produced a 60deg correction that tore
    # the arms apart. Pass --synthetic to force the old battery.
    if not USE_LAST and "--synthetic" not in _ARGS:
        # v1.199.109: default to a SPREAD of real library poses. Defaulting to the
        # newest last_pose_run dump meant grading against a sample of one -- and
        # it happened to be a rear-facing pose, the least informative case there
        # is. --last still forces the dump when reproducing a specific failure.
        try:
            from backend.services.character_studio.vnccs_native import workflows as _wf2
            _all = list(_wf2.creator_baseline_pose_data().get("poses") or [])
        except Exception:  # noqa: BLE001
            _all = []
        # v1.199.116: sample the AUDITED-SAFE poses when pose_audit has run.
        # Library poses 0 and 1 measure 49-57% arm-in-torso on a wide body even
        # with production re-skin + abduction -- they are excluded from the
        # recommended set, so grading depth quality on them just reproduces a
        # known-rejected input ("lots of missing arms" is those poses working
        # as measured, not a render bug).
        _idx = list(range(len(_all)))
        try:
            _par = json.loads((REPO / "_diag" / "pose_audit" / "report.json")
                              .read_text(encoding="utf-8"))
            _rec = [int(i) for i in (_par.get("recommended_poses") or [])
                    if 0 <= int(i) < len(_all)]
            if _rec:
                _idx = _rec
                print(f"using pose_audit recommended poses: {_idx[:4]}")
        except Exception:  # noqa: BLE001
            pass
        _lib = [(_i, _all[_i]) for _i in _idx[:4]]
        if _lib:
            BATTERY = [(f"{_i:02d}_lib", (p or {}).get("bones") or {},
                        (p or {}).get("modelRotation") or [0, 0, 0])
                       for _i, p in _lib]
            report["battery_source"] = (f"pose library indices {[i for i, _ in _lib]}"
                                        + (" (pose_audit recommended)"
                                           if _idx != list(range(len(_all))) else ""))
            print(f"using {len(_lib)} REAL library poses: {[i for i, _ in _lib]}")
        else:
            _p, _src = load_last_poses()
            if _p:
                BATTERY = _p
                report["battery_source"] = f"last_pose_run/{_src} ({len(_p)} real pose(s))"
                print(f"using the REAL poses from _diag/last_pose_run/{_src}  ({len(_p)})")
            else:
                report["battery_source"] = "synthetic (no library, no last_pose_run dump)"
    if USE_LAST:
        _p, _src = load_last_poses()
        if _p:
            BATTERY = _p
            report["battery_source"] = f"last_pose_run/{_src} ({len(_p)} pose(s))"
            print(f"using the REAL poses from _diag/last_pose_run/{_src}  ({len(_p)})")
        else:
            print("  !! --last: no last_pose_run dump with poses; using the synthetic battery")

    print("[1/4] mannequin shaded ...")
    run_mannequin("shaded", report)
    print("[2/4] mannequin DEPTH ...")
    run_mannequin("depth", report)

    report["data_dir"] = str(data_dir())
    fbx = find_rigged_fbx(CHAR)
    report["rigged_fbx"] = str(fbx) if fbx else None
    if fbx:
        # Proof-of-re-rig: rerig_all.bat backs the old sheared bind up as
        # rigged_prev_reset.fbx, so its presence (plus a newer rigged.fbx) is how
        # we know the v1.199.70 no-rest fix actually reached THIS character.
        import datetime as _dt
        prev = fbx.parent / "rigged_prev_reset.fbx"
        report["rig_mtime"] = _dt.datetime.fromtimestamp(fbx.stat().st_mtime).isoformat(" ", "seconds")
        report["rig_rerigged"] = prev.exists()
        if prev.exists():
            report["rig_prev_mtime"] = _dt.datetime.fromtimestamp(
                prev.stat().st_mtime).isoformat(" ", "seconds")
    if fbx:
        print("[3/6] clay shaded            (Blender, ~1-2 min) ...")
        run_clay(fbx, "shaded", report)
        print("[4/6] clay DEPTH  preserve_volume OFF  (~1-2 min) ...")
        run_clay(fbx, "depth", report)
        print("[5/6] clay DEPTH  auto-abduct OFF (control, ~1-2 min) ...")
        run_clay(fbx, "depth", report, tag="_noabduct", auto_abduct=False)
        print("[6/6] clay NORMAL (matcap surface normals, v1.199.115) ...")
        run_clay(fbx, "normal", report)
    else:
        print(f"  !! no rigged.fbx for {CHAR} under {data_dir()} -- clay half skipped")

    (OUT / "depth_report.json").write_text(json.dumps(report, indent=2, default=str),
                                           encoding="utf-8")
    print("\n--- RIG ---")
    print(f"  fbx        : {report.get('rigged_fbx')}")
    print(f"  modified   : {report.get('rig_mtime')}")
    print(f"  re-rigged  : {report.get('rig_rerigged')} "
          f"(rigged_prev_reset.fbx present = rerig_all has run)")
    for mode in ("shaded", "depth", "depth_noabduct", "normal"):
        for ln in (report.get(f"clay_{mode}_log") or []):
            if any(k in ln for k in ("maxstretch", "preserve_volume", "weld", "reskin",
                                     "rest edges", "CLAY_DISP", "CLAY_PENETRATION",
                                     "normal matcap")):
                print(f"  clay {mode:9s}: {ln}")
    print("  ^ compare clay depth (orphan repair ON) vs depth_raw (control, OFF).")
    print("    maxstretch in the thousands = unweighted verts still pinned at bind.")
    print("\n--- SUMMARY (spread < 10 = the depth pass did NOT work) ---")
    for k in ("mann_depth_stats", "clay_depth_stats", "clay_depth_noabduct_stats",
              "clay_normal_stats", "mann_shaded_stats", "clay_shaded_stats"):
        for label, st in (report.get(k) or {}).items():
            print(f"  {k.replace('_stats',''):18s} {label:12s} {st}")
    print("\nwrote ->", OUT)
    print('Tell Claude: "depth test done"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
