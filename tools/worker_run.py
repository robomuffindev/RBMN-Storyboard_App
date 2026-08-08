#!/usr/bin/env python3
"""Replay a real Klein pose job against a real ComfyUI worker, with overrides.

WHY THIS EXISTS: this session could render clay/depth locally but could not run
the fleet, so every model-side question ("does cfg 5 hurt likeness?", "does
PuLID help?") had to be relayed through a human describing pictures. That is
slow and lossy. This closes the loop: it re-submits the EXACT graph the app last
sent -- from _diag/last_pose_run/<ts>/graph.json -- changing ONE variable at a
time, and writes the outputs where they can be inspected directly.

  worker_run.bat                          replay the newest run, unchanged
  worker_run.bat --set cfg=1.5
  worker_run.bat --set steps=28 --set lora=0.8
  worker_run.bat --sweep cfg=1.5,2.5,3.5,5.0
  worker_run.bat --run 20260726_155010 --set ref_end=1.0
  worker_run.bat --list

Overrides (--set k=v, repeatable; --sweep k=v1,v2,... runs one job per value):
  cfg       CFGGuider.cfg                       steps   Flux2Scheduler.steps
  lora      LoraLoaderModelOnly.strength_model  seed    RandomNoise.noise_seed
  ref_end   ConditioningSetTimestepRange split point (1.0 = hold refs all run)
  unet      UNETLoader.unet_name                denoise SplitSigmasDenoise.denoise

Out: <repo>/_diag/worker_run/<tag>/  -> out_*.png + report.json
Then tell Claude: "worker run done".
"""
from __future__ import annotations
import copy, json, os, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "_diag" / "worker_run"
RUNS = REPO / "_diag" / "last_pose_run"


def workers() -> list:
    db = Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT comfyui_urls FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                raw = str(row[0]).strip()
                v = json.loads(raw) if raw.startswith("[") else raw.split(",")
                return [str(x).strip() for x in v if str(x).strip()]
        except Exception:  # noqa: BLE001
            pass
    return []


def pick_run(name: str | None):
    if not RUNS.is_dir():
        return None
    dirs = sorted([d for d in RUNS.iterdir() if d.is_dir() and (d / "graph.json").exists()])
    if not dirs:
        return None
    if name:
        for d in dirs:
            if d.name == name:
                return d
        return None
    return dirs[-1]


def apply_override(api: dict, key: str, val: str) -> int:
    """Patch every node the override applies to. Returns how many were touched --
    0 means the override silently did nothing, which the report makes loud."""
    n = 0
    for nid, node in api.items():
        ct = node.get("class_type")
        ins = node.get("inputs") or {}
        if key == "cfg" and ct == "CFGGuider":
            ins["cfg"] = float(val); n += 1
        elif key == "steps" and ct == "Flux2Scheduler":
            ins["steps"] = int(float(val)); n += 1
        elif key == "lora" and ct == "LoraLoaderModelOnly" and "strength_model" in ins:
            ins["strength_model"] = float(val); n += 1
        elif key == "seed" and ct == "RandomNoise":
            ins["noise_seed"] = int(float(val)); n += 1
        elif key == "unet" and ct == "UNETLoader":
            ins["unet_name"] = str(val); n += 1
        elif key == "denoise" and ct == "SplitSigmasDenoise":
            ins["denoise"] = float(val); n += 1
        elif key == "ref_end" and ct == "ConditioningSetTimestepRange":
            v = float(val)
            # the pair is (0..rel) and (rel..1); 1.0 collapses to "hold the refs"
            if float(ins.get("start", 0.0)) == 0.0:
                ins["end"] = v
            else:
                ins["start"] = v
            n += 1
    return n


def run_one(host: str, api: dict, imgs: dict, tag: str, report: dict) -> None:
    from backend.services.character_studio.vnccs_native.client import VNCCSClient  # noqa: PLC0415
    cli = VNCCSClient(host, timeout=60)
    for fn, data in imgs.items():
        try:
            cli.upload_image(fn, data, "", True, 120)
        except Exception as e:  # noqa: BLE001
            report.setdefault("upload_errors", []).append(f"{fn}: {e}")
    res = cli.submit_prompt(api, timeout=120)
    pid = res.get("prompt_id")
    report[f"{tag}_prompt_id"] = pid
    print(f"    submitted {pid}; waiting ...")
    t0 = time.time()
    hist = {}
    while time.time() - t0 < 900:
        time.sleep(3)
        try:
            h = cli.get_history(pid, timeout=30) or {}
        except Exception:  # noqa: BLE001
            continue
        h = h.get(pid) or h
        if h and (h.get("outputs") or (h.get("status") or {}).get("completed")):
            hist = h
            break
    if not hist:
        report[f"{tag}_error"] = "timed out after 900s"
        print("    TIMED OUT")
        return
    st = (hist.get("status") or {})
    report[f"{tag}_status"] = st.get("status_str") or st.get("completed")
    saved = 0
    for _nid, out in (hist.get("outputs") or {}).items():
        for im in (out.get("images") or []):
            try:
                data = cli.view_image(im.get("filename"), im.get("subfolder", ""),
                                      im.get("type", "output"), timeout=120)
                dst = OUT / f"out_{tag}_{saved:02d}.png"
                dst.write_bytes(data)
                saved += 1
            except Exception as e:  # noqa: BLE001
                report.setdefault(f"{tag}_dl_errors", []).append(str(e))
    report[f"{tag}_images"] = saved
    print(f"    {saved} image(s) -> {OUT}")


def main() -> int:
    # Write a crash log no matter what: the first real invocation produced an
    # EMPTY _diag/worker_run with no report, which tells the reader nothing.
    try:
        return _main()
    except Exception:
        import traceback
        OUT.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (OUT / "report.json").write_text(json.dumps({"fatal": tb}, indent=2), encoding="utf-8")
        print(tb)
        print("\nwrote crash report ->", OUT)
        return 2


def _main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        for d in sorted([d for d in RUNS.iterdir() if d.is_dir()]) if RUNS.is_dir() else []:
            print(d.name, "graph" if (d / "graph.json").exists() else "(no graph)")
        return 0
    run_name = None
    sets, sweep = {}, None
    host_arg = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--run" and i + 1 < len(args):
            run_name = args[i + 1]; i += 2; continue
        if a == "--host" and i + 1 < len(args):
            host_arg = args[i + 1]; i += 2; continue
        if a == "--set" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("="); sets[k.strip()] = v.strip(); i += 2; continue
        if a == "--sweep" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            sweep = (k.strip(), [x.strip() for x in v.split(",") if x.strip()]); i += 2; continue
        i += 1

    d = pick_run(run_name)
    if d is None:
        print("ERROR: no _diag/last_pose_run/<ts>/graph.json yet. Run one pose set from the "
              "app first (v1.199.96+ dumps the graph), then re-run this.")
        return 1
    base_api = json.loads((d / "graph.json").read_text(encoding="utf-8"))
    replay = {}
    if (d / "replay.json").exists():
        replay = json.loads((d / "replay.json").read_text(encoding="utf-8"))

    # every LoadImage filename the graph needs, matched to a file in the dump
    want = {str((n.get("inputs") or {}).get("image"))
            for n in base_api.values() if n.get("class_type") == "LoadImage"}
    imgs = {}
    for group, pref in (("pose_files", "ref_"), ("identity_files", "identity_"),
                        ("init_files", "init_")):
        for idx, fn in enumerate(replay.get(group) or []):
            f = d / f"{pref}{idx:02d}.png"
            if fn in want and f.exists():
                imgs[fn] = f.read_bytes()
    missing = sorted(want - set(imgs))

    host = host_arg or replay.get("host") or (workers()[0] if workers() else None)
    if not host:
        print("ERROR: no worker URL (pass --host http://...:8188/)")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    report = {"source_run": d.name, "host": host, "overrides": sets,
              "sweep": sweep, "images_reuploaded": sorted(imgs),
              "images_assumed_still_on_worker": missing}
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"replaying {d.name} on {host}")
    if missing:
        print(f"  NOTE: {len(missing)} input image(s) not in the dump; relying on the "
              f"worker's input dir still holding them: {missing[:4]}")

    jobs = []
    if sweep:
        k, vals = sweep
        for v in vals:
            jobs.append((f"{k}{v}".replace(".", "p"), {**sets, k: v}))
    else:
        jobs.append(("base" if not sets else "_".join(f"{k}{v}" for k, v in sets.items()).replace(".", "p"), sets))

    for tag, ov in jobs:
        api = copy.deepcopy(base_api)
        touched = {k: apply_override(api, k, v) for k, v in ov.items()}
        report[f"{tag}_overrides_applied"] = touched
        dead = [k for k, n in touched.items() if n == 0]
        if dead:
            print(f"  WARNING: override(s) matched NO nodes and did nothing: {dead}")
        print(f"  [{tag}] {ov or 'unchanged'}")
        try:
            run_one(host, api, imgs, tag, report)
        except Exception as e:  # noqa: BLE001
            report[f"{tag}_error"] = str(e)
            print(f"    ERROR {e}")

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\nwrote ->", OUT)
    print('Tell Claude: "worker run done"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
