#!/usr/bin/env python3
"""Collect everything needed to debug recent Klein pose runs into _diag/pose_diag/.

Gathers, with NO user explanation needed:
  1. _diag/last_pose_run/*  -- the EXACT refs Klein saw + every knob (mesh,
     lora, strength, ref_end, cfg, steps), auto-dumped by the app per run
     since v1.199.80 (restart run.bat once to activate).
  2. The newest 12 pose OUTPUT sprites (DB `asset` rows whose filename matches
     %klein_sprites%), copied with their timestamps.
  3. A log excerpt of the pose-relevant lines (knobs, fit, errors, tracebacks).

Run pose_diag.bat after a bad run, then tell Claude "diag done".  Claude reads
_diag/pose_diag/ through the connected folder and sees refs vs outputs vs
settings side by side.
"""
from __future__ import annotations
import json, os, shutil, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "pose_diag"
N_SPRITES = 12


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


def main():
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) refs + params (app auto-dump)
    lpr = REPO / "_diag" / "last_pose_run"
    if lpr.is_dir():
        shutil.copytree(lpr, OUT / "last_pose_run")
        print("copied last_pose_run:", sorted(d.name for d in lpr.iterdir()))
    else:
        print("NOTE: no _diag/last_pose_run yet (app must run a pose set on >= v1.199.80)")

    # 2) newest output sprites via the DB
    data = project_dir()
    db = Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db"
    got = 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "SELECT rel_path, filename, project_id FROM assets WHERE filename LIKE '%klein_sprites%' "
            "OR rel_path LIKE '%klein_sprites%' OR filename LIKE '%sprites%' "
            "ORDER BY id DESC LIMIT ?", (N_SPRITES,)).fetchall()
        if not rows:
            sample = con.execute(
                "SELECT rel_path, filename, project_id FROM assets ORDER BY id DESC LIMIT 12").fetchall()
            (OUT / "asset_sample.txt").write_text(
                "\n".join(" | ".join(str(x) for x in r) for r in sample), encoding="utf-8")
            print("no sprite filename match -- newest asset rows dumped to asset_sample.txt")
        con.close()
        (OUT / "outputs").mkdir(exist_ok=True)
        unresolved = []
        for rel, fn, proj_id in rows:
            # ingest stores files at <project_dir>/<project_uuid>/<rel_path>
            home = Path.home() / "RBMN-Projects"   # assets may live under the C: default root
            rel_n = str(rel).replace("\\", "/")
            cands = (data / str(proj_id) / rel_n, home / str(proj_id) / rel_n,
                     data / "assets" / rel_n, data / rel_n, home / rel_n, Path(rel_n))
            for cand in cands:
                if cand.exists():
                    ts = time.strftime("%m%d_%H%M%S", time.localtime(cand.stat().st_mtime))
                    shutil.copy2(cand, OUT / "outputs" / f"{ts}_{fn}")
                    got += 1
                    break
            else:
                unresolved.append(f"{proj_id} | {rel}")
        if unresolved:
            (OUT / "unresolved.txt").write_text("\n".join(unresolved), encoding="utf-8")
        if got == 0:
            # v1.199.89 FALLBACK: forget the <project_uuid>/<rel_path> arithmetic --
            # the uuid in the assets table is dash-stripped while the folder on disk
            # keeps its dashes (and preview runs may never be ingested as assets at
            # all). Just sweep the data dir for sprite PNGs and take the newest by
            # mtime, which is what we actually want: "show me the last run's output".
            print("path arithmetic found nothing -- sweeping data dir by mtime ...")
            hits = []
            for root in {data, Path.home() / "RBMN-Projects"}:
                if not root.is_dir():
                    continue
                for pat in ("**/sprites/*.png", "**/original_sprites/*.png"):
                    try:
                        hits.extend(root.glob(pat))
                    except Exception:  # noqa: BLE001
                        pass
            hits = sorted({h for h in hits if h.is_file()},
                          key=lambda x: x.stat().st_mtime, reverse=True)[:12]
            for cand in hits:
                ts = time.strftime("%m%d_%H%M%S", time.localtime(cand.stat().st_mtime))
                shutil.copy2(cand, OUT / "outputs" / f"{ts}_{cand.name}")
                got += 1
            if hits:
                (OUT / "outputs_source.txt").write_text(
                    "\n".join(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(h.stat().st_mtime))}"
                              f"  {h}" for h in hits), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("WARN sprite copy:", e)
    print(f"copied {got} output sprite(s)")
    (OUT / "report.txt").write_text(
        f"sprites_copied={got}\ndata_dir={data}\n", encoding="utf-8")

    # 3) log excerpt
    log = REPO / "logs" / "rbmn.log"
    if log.exists():
        keys = ("klein pose", "klein cleanup", "pose-ref release", "3D-scan body fit",
                "klein autofit", "pose_clay", "ERROR", "Traceback", "klein models",
                "klein pose input", "consistency LoRA", "diag dump")
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
        keep = [ln for ln in lines if any(k in ln for k in keys)][-300:]
        (OUT / "log_excerpt.txt").write_text("\n".join(keep), encoding="utf-8")
        print("log excerpt:", len(keep), "lines")
    print("wrote ->", OUT)
    print("Tell Claude: diag done")


if __name__ == "__main__":
    main()
