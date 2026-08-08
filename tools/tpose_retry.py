#!/usr/bin/env python3
"""TPOSE RETRY (v1.199.136) -- re-run the T-pose EDIT on ONE saved view.

The T-pose pass (base-set, engine=edit) writes every input/output pair to
`_diag/tpose/<run>_<view>/`.  This replays the edit for a single view against a
real worker, so iterating the wording costs ONE image instead of a whole
mesh-ready set (8 worker images) -- and the text it uses is the SAME
`klein_poses.tpose_edit_prompt` production uses, so a win here is a win there.

  tpose_retry.bat back                     newest saved BACK input, current prompt
  tpose_retry.bat back --prompt-file p.txt same input, YOUR wording (iterate free)
  tpose_retry.bat back --in path\\to.png     an explicit input image
  tpose_retry.bat back --seed 12345 --n 3  three seeds, same prompt

Writes `_diag/tpose_retry/<stamp>/` (in.png, out_NN.png, prompt.txt, report.json)
and prints the prompt it sent.
"""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "_diag" / "tpose_retry" / time.strftime("%Y%m%d_%H%M%S")


def workers() -> list:
    """Worker URLs from the DB (app_settings.comfyui_urls) -- .env is stale."""
    for db in (Path.home() / "RBMN-Projects" / "RBMN.db",):
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT comfyui_urls FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                raw = str(row[0]).strip()
                v = json.loads(raw) if raw.startswith("[") else raw.split(",")
                return [str(x).strip() for x in v if str(x).strip()]
        except Exception as e:  # noqa: BLE001
            print("WARN app_settings:", e)
    return []


def newest_input(view: str):
    root = REPO / "_diag" / "tpose"
    if not root.is_dir():
        return None
    hits = sorted((d for d in root.iterdir()
                   if d.is_dir() and d.name.lower().endswith("_" + view.lower())
                   and (d / "in.png").exists()),
                  key=lambda d: (d / "in.png").stat().st_mtime, reverse=True)
    return (hits[0] / "in.png") if hits else None


def main() -> int:
    args = list(sys.argv[1:])
    view = "back"
    if args and not args[0].startswith("-"):
        view = args.pop(0).strip().lower()

    def opt(name, default=None):
        if name in args:
            i = args.index(name)
            v = args[i + 1] if i + 1 < len(args) else None
            del args[i:i + 2]
            return v
        return default

    in_path = opt("--in")
    prompt_file = opt("--prompt-file")
    seed = int(opt("--seed", "0") or 0)
    n = int(opt("--n", "1") or 1)
    host = opt("--host")

    src = Path(in_path) if in_path else newest_input(view)
    if not src or not src.exists():
        print(f"ERROR: no saved input for view {view!r}.\n"
              f"  Looked in {REPO / '_diag' / 'tpose'} for *_{view}/in.png -- generate a\n"
              f"  mesh-ready set once with klein_mesh_tpose_engine=edit (the default) and\n"
              f"  every view's input/output pair is kept there.  Or pass --in <file>.")
        return 2

    if prompt_file:
        prompt = Path(prompt_file).read_text(encoding="utf-8").strip()
        print(f"prompt: from {prompt_file}")
    else:
        from backend.services.character_studio.vnccs_native import klein_poses
        prompt = klein_poses.tpose_edit_prompt(view)
        print("prompt: klein_poses.tpose_edit_prompt(%r)  [production text]" % view)

    hosts = ([host] if host else workers())
    if not hosts:
        print("ERROR: no worker URLs in app_settings.comfyui_urls")
        return 2

    from PIL import Image
    with Image.open(src) as im:
        w, h = im.size
    print(f"input : {src}  ({w}x{h})")
    print(f"worker: {hosts[0]}")
    print("-" * 78)
    print(prompt)
    print("-" * 78)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "in.png").write_bytes(src.read_bytes())
    (OUT / "prompt.txt").write_text(prompt, encoding="utf-8")

    from backend.services.character_studio.vnccs_native.client import VNCCSClient
    from backend.services.comfyui.workflow import prepare_klein_workflow
    cli = VNCCSClient(hosts[0], timeout=60)
    up = cli.upload_image("rbmn_tpose_retry_src.png", src.read_bytes(), "", True, 120)
    name = up.get("name") or "rbmn_tpose_retry_src.png"

    wf_path = REPO / "workflows" / "KLEIN_EDIT_ULTRA_WORKFLOW_1REF.json"
    report = {"view": view, "input": str(src), "host": hosts[0], "images": []}
    for k in range(max(1, n)):
        sd = (seed or int(time.time())) + k * 7919
        wf = prepare_klein_workflow(str(wf_path), prompt, int(w), int(h), int(sd),
                                    ref_images=[name])
        res = cli.submit_prompt(wf, timeout=120)
        pid = res.get("prompt_id") if isinstance(res, dict) else None
        if not pid:
            print(f"  seed {sd}: submit failed")
            continue
        print(f"  seed {sd}: submitted {pid}; waiting ...")
        t0, hist = time.time(), {}
        while time.time() - t0 < 600:
            time.sleep(3)
            try:
                hh = cli.get_history(pid, timeout=30) or {}
            except Exception:  # noqa: BLE001
                continue
            hh = hh.get(pid) or hh
            if hh and (hh.get("outputs") or (hh.get("status") or {}).get("completed")):
                hist = hh
                break
        if not hist:
            print("    TIMED OUT")
            continue
        got = 0
        for _nid, o in (hist.get("outputs") or {}).items():
            for im in (o.get("images") or []):
                try:
                    data = cli.view_image(im.get("filename"), im.get("subfolder", ""),
                                          im.get("type", "output"), timeout=120)
                    dst = OUT / f"out_{k:02d}.png"
                    dst.write_bytes(data)
                    report["images"].append({"seed": sd, "file": dst.name})
                    got += 1
                except Exception as e:  # noqa: BLE001
                    print("    download failed:", e)
        print(f"    {got} image(s)")
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    print("OPEN THE IMAGES.  Back view must show: back of head, shoulder blades, spine,\n"
          "seat of the underwear, backs of the legs -- and NO face, chest, nipples or navel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
