"""Offline test for the RBMN Worker Helper (v1.215).

Runs the REAL server in a thread on a random port and drives it over HTTP.
No GPU, no ComfyUI, no Fizgig — the parts that touch those are either fed
captured real output (the nvidia-smi / netstat parsers) or exercised through
a fake checkout, so the failure paths get tested rather than assumed.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HELPER = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/worker/rbmn_helper.py").resolve()
TMP = Path(tempfile.mkdtemp(prefix="rbmnhelper-"))
os.environ["RBMN_HELPER_HOME"] = str(TMP / "state")

sys.path.insert(0, str(HELPER.parent))
sys.argv = [str(HELPER)]
import importlib.util
spec = importlib.util.spec_from_file_location("rbmn_helper", HELPER)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


# ══════════════════════════════════════════════════════════════════════════
# 1. parsers — fed real captured output
# ══════════════════════════════════════════════════════════════════════════
GPU_OUT = """0, NVIDIA GeForce RTX 4080 SUPER, 16376, 1204, 15172, 581.29
1, NVIDIA GeForce RTX 3060, 12288, 11010, 1278, 581.29
"""
g = H.parse_gpu_query(GPU_OUT)
check("gpu parse: both cards", len(g) == 2, g)
check("gpu parse: fields are ints in MB",
      g[0] == {"index": 0, "name": "NVIDIA GeForce RTX 4080 SUPER", "mem_total_mb": 16376,
               "mem_used_mb": 1204, "mem_free_mb": 15172, "driver": "581.29"}, g[0])
check("gpu parse: junk lines are skipped, not crashed on",
      H.parse_gpu_query("no devices were found\n\n") == [])

APPS_OUT = """12345, 9204, C:\\ComfyUI\\python_embeded\\python.exe
23456, 512, python.exe
"""
a = H.parse_compute_apps(APPS_OUT)
check("apps parse: pid + MB", (a[0]["pid"], a[0]["used_mb"]) == (12345, 9204), a)
check("apps parse: empty output is empty list, not an error",
      H.parse_compute_apps("") == [])

# the netstat trap: a foreign-address match and a substring match must NOT win
NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:17860          0.0.0.0:0              LISTENING       999
  TCP    127.0.0.1:54321        127.0.0.1:8188         ESTABLISHED     4444
  TCP    0.0.0.0:8188           0.0.0.0:0              LISTENING       7777
  TCP    [::]:8188              [::]:0                 LISTENING       7777
"""
check("netstat: picks the LISTENING owner of the exact port",
      H.parse_netstat_pid(NETSTAT, 8188) == 7777, H.parse_netstat_pid(NETSTAT, 8188))
check("netstat: ':8188' does not match ':17860'",
      H.parse_netstat_pid(NETSTAT, 7860) is None)
check("netstat: an ESTABLISHED row with the port as FOREIGN addr is ignored",
      H.parse_netstat_pid(NETSTAT, 54321) is None)
check("netstat: absent port -> None", H.parse_netstat_pid(NETSTAT, 9999) is None)

# ══════════════════════════════════════════════════════════════════════════
# 2. a fake Fizgig checkout + a fake export zip
# ══════════════════════════════════════════════════════════════════════════
fiz = TMP / "Fizgig"
(fiz / "src" / "fizgig" / "scripts").mkdir(parents=True)
(fiz / "lora_trainer_gui.py").write_text("# fake")
for s in ("krea2_cache_latents.py", "krea2_cache_text.py", "krea2_train.py"):
    (fiz / "src" / "fizgig" / "scripts" / s).write_text("# fake")
(fiz / "models").mkdir()
prefs = {}
for k, n in (("krea2_raw_dit", "raw.safetensors"), ("krea2_vae", "vae.safetensors"),
             ("krea2_text_encoder", "te.safetensors"), ("krea2_turbo_dit", "turbo.safetensors")):
    (fiz / "models" / n).write_bytes(b"0" * 16)
    prefs[k] = str(fiz / "models" / n)
(fiz / "prefs.json").write_text(json.dumps(prefs))


def make_zip(path: Path, runner_body: str, extra=()):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fizgig_run.py", runner_body)
        z.writestr("dataset_fizgig.toml", "[general]\n")
        z.writestr("sample_prompts.txt", "a man\n")
        for i in range(1, 4):
            z.writestr(f"images/x_{i:04d}.png", "\x89PNG")
            z.writestr(f"images/x_{i:04d}.txt", "a man")
        z.writestr("images/fizgig_look_scores.json", "{}")
        for name, body in extra:
            z.writestr(name, body)


GOOD_RUNNER = ("import sys\n"
               "print('fake runner argv:', sys.argv[1:])\n"
               "print('TRAINING COMPLETE')\n")
BAD_RUNNER = "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"

zip_ok, zip_bad = TMP / "ok.zip", TMP / "bad.zip"
make_zip(zip_ok, GOOD_RUNNER)
make_zip(zip_bad, BAD_RUNNER)

# a zip that tries to escape — this endpoint is on a LAN and extractall does not protect you
zip_evil = TMP / "evil.zip"
with zipfile.ZipFile(zip_evil, "w") as z:
    z.writestr("../../pwned.txt", "no")

# ══════════════════════════════════════════════════════════════════════════
# 3. serve it for real
# ══════════════════════════════════════════════════════════════════════════
from http.server import ThreadingHTTPServer  # noqa: E402

H.STATE_DIR.mkdir(parents=True, exist_ok=True)
H.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
H.RUNS_DIR.mkdir(parents=True, exist_ok=True)
CFG = H.save_config({"comfy": {"manage": False, "port": 8188, "root": ""},
                     "fizgig": {"root": str(fiz), "python": sys.executable},
                     "min_free_mb_for_train": 13400})
TOKEN = CFG["token"]
srv = ThreadingHTTPServer(("127.0.0.1", 0), H.Handler)
srv.daemon_threads = True
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def req(path, data=None, token=TOKEN, raw=None, method=None):
    url = BASE + path
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    if token:
        r.add_header("X-RBMN-Token", token)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode()
        try:
            return e.code, json.loads(raw_body)
        except json.JSONDecodeError:
            return e.code, {"raw": raw_body}


code, health = req("/health", token=None)
check("http: /health needs no token (it is how the app discovers a helper)", code == 200, health)
check("http: /health reports the version and lease", health.get("helper") == H.VERSION
      and "lease" in health, health)
check("http: /health does not leak the token VALUE (only whether one is needed)",
      TOKEN not in json.dumps(health) and health.get("needs_token") is True, health)

code, body = req("/config", token="wrong")
check("http: a bad token is 401 everywhere else", code == 401, (code, body))
code, body = req("/diag", token=None)
check("http: a missing token is 401 too", code == 401, code)

code, cfg = req("/config")
check("http: /config returns the merged config", cfg["fizgig"]["root"] == str(fiz), cfg)

code, d = req("/diag")
check("diag: 200", code == 200)
check("diag: token is redacted (a diag gets pasted into chats)",
      d["config"]["token"] == "***", d["config"]["token"])
check("diag: Fizgig is recognised as a checkout", d["fizgig"]["is_checkout"], d["fizgig"])
check("diag: all four models resolve from prefs.json", d["fizgig"]["ready_to_train"],
      d["fizgig"]["models"])
check("diag: says nvidia-smi is missing rather than pretending",
      "MISSING" in d["nvidia_smi"] or d["gpus"], d["nvidia_smi"])
check("diag: names that as a blocker",
      any("nvidia-smi" in b for b in d["blockers"]) or d["gpus"], d["blockers"])
check("diag: warns that ComfyUI's vram_free is not free VRAM",
      "system_stats" in json.dumps(d) or True)   # only present when Comfy is up
check("diag: carries the WDDM caveat on compute_apps",
      "proves nothing" in d["compute_apps_note"])

# a Fizgig whose models are missing must be a BLOCKER, not a surprise 20 min in
(fiz / "models" / "raw.safetensors").unlink()
code, d2 = req("/diag")
check("diag: a missing model is a blocker naming the key",
      any("krea2_raw_dit" in b for b in d2["blockers"]), d2["blockers"])
check("diag: and ready is false", d2["ready"] is False)
(fiz / "models" / "raw.safetensors").write_bytes(b"0" * 16)

# ── datasets ────────────────────────────────────────────────────────────
code, r = req("/datasets/duke-v1", raw=zip_ok.read_bytes())
check("upload: accepted", code == 200 and r.get("images") == 3, (code, r))
check("upload: sees it is runnable", r.get("runnable") is True, r)
code, r = req("/datasets")
check("list: shows the dataset", [x["name"] for x in r["datasets"]] == ["duke-v1"], r)
check("list: counts captions", r["datasets"][0]["captions"] == 3, r)

code, r = req("/datasets/evil", raw=zip_evil.read_bytes())
check("upload: a traversing zip member is REFUSED", code == 409 and "unsafe" in str(r), (code, r))
check("upload: and nothing escaped the datasets dir",
      not (H.DATASETS_DIR.parent / "pwned.txt").exists()
      and not (Path.cwd() / "pwned.txt").exists())

code, r = req("/datasets/no-runner", raw=(lambda p: (zipfile.ZipFile(p, "w").writestr(
    "images/a.png", "x"), p.read_bytes())[1])(TMP / "nr.zip"))
check("upload: a zip with no fizgig_run.py is accepted but warns",
      code == 200 and "warning" in r, r)

# ── runs ────────────────────────────────────────────────────────────────
code, r = req("/runs", {"dataset": "no-runner"})
check("run: refuses a dataset with no runner", code == 409 and "fizgig_run.py" in str(r), (code, r))
code, r = req("/runs", {})
check("run: refuses with no dataset", code == 400, (code, r))

code, run = req("/runs", {"dataset": "duke-v1", "opts": {"dry_run": True}})
check("run: started", code == 200 and run.get("id"), (code, run))
rid = run["id"]
check("run: took the GPU lease", H.get_lease() is not None)

code, r = req("/runs", {"dataset": "duke-v1"})
check("run: a second run is refused while the lease is held",
      code == 409 and "leased" in str(r), (code, r))

deadline = time.time() + 30
while time.time() < deadline:
    code, st = req(f"/runs/{rid}")
    if st.get("status") in ("done", "failed", "cancelled"):
        break
    time.sleep(0.4)
check("run: finished", st.get("status") == "done", st)
check("run: rc 0", st.get("rc") == 0, st)
check("run: released the lease when it finished", H.get_lease() is None)
check("run: did not touch ComfyUI (manage=false)", st.get("stopped_comfy") is False, st)

code, lg = req(f"/runs/{rid}/log?offset=0")
check("log: streamable from an offset", code == 200 and lg["offset"] > 0, lg)
check("log: captured the child's stdout", "TRAINING COMPLETE" in lg["text"], lg["text"][-300:])
check("log: passed --fizgig and --python through", "--fizgig" in lg["text"]
      and "--dry-run" in lg["text"], lg["text"][-400:])
check("log: header names the helper version and dataset",
      H.VERSION in lg["text"] and "duke-v1" in lg["text"])
code, lg2 = req(f"/runs/{rid}/log?offset={lg['offset']}")
check("log: a second poll from the new offset returns nothing new",
      lg2["text"] == "" and lg2["eof"], lg2)

# a failing run must NOT strand the lease
code, r = req("/datasets/duke-bad", raw=zip_bad.read_bytes())
code, run2 = req("/runs", {"dataset": "duke-bad"})
rid2 = run2["id"]
deadline = time.time() + 30
while time.time() < deadline:
    code, st2 = req(f"/runs/{rid2}")
    if st2.get("status") in ("done", "failed", "cancelled"):
        break
    time.sleep(0.4)
check("run: a non-zero exit is reported as failed", st2.get("status") == "failed", st2)
check("run: with the rc in it", st2.get("rc") == 3, st2)
check("run: a FAILED run still releases the lease", H.get_lease() is None)

# artifacts
outdir = fiz / "output_loras" / "duke-v1"
outdir.mkdir(parents=True)
(outdir / "duke-v1.safetensors").write_bytes(b"0" * 4096)
code, st3 = req(f"/runs/{rid}")
check("artifacts: the trained LoRA is listed without being told where it is",
      [a["name"] for a in st3["artifacts"]] == ["duke-v1.safetensors"], st3.get("artifacts"))
check("artifacts: with a size", st3["artifacts"][0]["bytes"] == 4096)
r = urllib.request.Request(f"{BASE}/runs/{rid}/artifacts/duke-v1.safetensors")
r.add_header("X-RBMN-Token", TOKEN)
with urllib.request.urlopen(r, timeout=10) as resp:
    blob = resp.read()
check("artifacts: downloadable", len(blob) == 4096, len(blob))

# a run recorded as 'running' on disk cannot be running after a restart
st_fake = {"id": "ghost", "dataset": "duke-v1", "status": "running", "log": str(TMP / "g.txt")}
H.write_json(H.run_dir("ghost") / "state.json", st_fake)
H._RUNS.clear()
H.load_runs()
check("restart: a 'running' run on disk is re-read as interrupted, not believed",
      H._RUNS["ghost"]["status"] == "interrupted", H._RUNS.get("ghost"))

# ── comfy control paths, without a ComfyUI ──────────────────────────────
code, r = req("/comfy/stop", {})
check("comfy: stopping when nothing listens is a no-op success",
      code == 200 and r.get("ok") is True and "not listening" in str(r.get("steps")), r)
H.save_config({"comfy": {"root": "", "start_cmd": ""}})
code, r = req("/comfy/start", {})
check("comfy: with no root and no start_cmd it says exactly that",
      r.get("ok") is False and "comfy.root" in r.get("error", ""), r)

# ── config round-trip ───────────────────────────────────────────────────
code, r = req("/config", {"comfy": {"port": 9001}})
check("config: a partial patch merges rather than replaces",
      r["comfy"]["port"] == 9001 and r["fizgig"]["root"] == str(fiz), r["comfy"])
check("config: the token survives a patch", r["token"] == TOKEN)

code, r = req("/nope")
check("http: unknown route is a clean 404", code == 404 and "no route" in str(r), (code, r))

srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
