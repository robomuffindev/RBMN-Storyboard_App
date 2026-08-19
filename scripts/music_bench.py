"""🎚 Music bench — where does a music render's wall clock actually go?

"MiniMax sounds better but takes a long time" is a wall-clock complaint, and
wall clock on this fleet is two very different things stacked:

    submit -> execution_start     the box LOADING 12 GB of weights
    execution_start -> success    the actual sampling

ComfyUI's /history carries both timestamps, so this separates them instead of
guessing. It renders the SAME prompt twice per box: the first pays the model
load, the second finds the weights already resident. If the gap between run 1
and run 2 is large, the fix is scheduling (keep a box on one engine, queue
cues back-to-back) rather than anything about the model.

Usage:
  python scripts\\music_bench.py --engine minimax3 --seconds 15
  python scripts\\music_bench.py --engine ace15 --seconds 20 --hosts 192.168.12.163
  python scripts\\music_bench.py --engine ace15 --steps 50 --cfg 6 --seconds 20

⚠ This talks to the WORKERS directly (not the app), so it does not touch the
Audio Lab job board — nothing to clean up afterwards. ⚠⚠ It therefore BYPASSES
`enqueue_music`, which means **no per-engine prompt shaping and no loudness
normalisation**: bench output is raw. Compare bench renders only against other
bench renders, and run them through `scripts/audio_level_match.py` before
judging a mix — an unmatched A/B is a loudness test.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.api.audio_lab import (ACE_XL, _ace_graph,  # noqa: E402
                                   _ace_xl_graph, _engine_status, _hosts,
                                   _mm3_graph)

TAGS = ("cinematic western instrumental, baritone guitar, sparse brushed "
        "percussion, distant harmonica, wide dry room")


def jpost(url: str, body: dict, timeout: float = 120.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: "
                           f"{e.read().decode('utf-8', 'replace')[:400]}") from None


def jget(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def stamps(h: dict) -> tuple[float, float]:
    """(execution_start, execution_success) in seconds, from history messages."""
    start = end = 0.0
    for m in ((h.get("status") or {}).get("messages") or []):
        if not (isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict)):
            continue
        t = m[1].get("timestamp")
        if not t:
            continue
        t = float(t) / 1000.0 if float(t) > 1e11 else float(t)
        if m[0] == "execution_start":
            start = t
        elif m[0] in ("execution_success", "execution_error"):
            end = t
    return start, end


def one(host: str, graph: dict, label: str, save: str = "") -> dict:
    base = f"http://{host}:8188"
    t0 = time.time()
    r = jpost(f"{base}/prompt", {"prompt": graph})
    pid = r.get("prompt_id")
    if not pid:
        raise RuntimeError(f"no prompt_id: {str(r)[:200]}")
    while time.time() - t0 < 1800:
        time.sleep(1.5)
        h = (jget(f"{base}/history/{pid}", timeout=20) or {}).get(pid)
        if not h:
            continue
        st = (h.get("status") or {}).get("status_str")
        if st in ("success", "error"):
            wall = time.time() - t0
            s, e = stamps(h)
            exe = round(e - s, 1) if (s and e) else 0.0
            load = round(wall - exe, 1) if exe else 0.0
            out = ""
            if save and st == "success":
                out = fetch_audio(base, h, save, label, host)
            print(f"  {label:<26} {host:<16} wall {wall:6.1f}s"
                  f"   exec {exe:6.1f}s   load/queue {load:6.1f}s"
                  + (f"   -> {out}" if out else "")
                  + ("" if st == "success" else f"   ⚠ {st}"))
            return {"host": host, "label": label, "wall": round(wall, 1),
                    "exec": exe, "load": load, "status": st, "file": out}
    raise TimeoutError("no result in 30 min")


def fetch_audio(base: str, h: dict, save: str, label: str, host: str) -> str:
    """Pull the rendered mp3 off the worker so the bench produces something
    LISTENABLE — a timing table cannot answer a question about quality."""
    for node_out in (h.get("outputs") or {}).values():
        for key in ("audio", "audios", "files"):
            for f in (node_out.get(key) or []):
                fn = f.get("filename")
                if not fn:
                    continue
                q = urllib.parse.urlencode({"filename": fn,
                                            "subfolder": f.get("subfolder", ""),
                                            "type": f.get("type", "output")})
                with urllib.request.urlopen(f"{base}/view?{q}", timeout=300) as r:
                    data = r.read()
                d = Path(save)
                d.mkdir(parents=True, exist_ok=True)
                safe = label.replace(" ", "_").replace("(", "").replace(")", "")
                fp = d / f"{safe}_{host.split('.')[-1]}{Path(fn).suffix or '.mp3'}"
                fp.write_bytes(data)
                return fp.name
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="minimax3",
                    choices=("minimax3", "ace15", "ace15_sft", "ace15_base"))
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--hosts", default="", help="comma list; default = every ready box")
    ap.add_argument("--repeat", type=int, default=2,
                    help="renders per box (run 1 pays the model load)")
    ap.add_argument("--steps", type=int, default=0, help="override KSampler steps")
    ap.add_argument("--cfg", type=float, default=0.0, help="override KSampler cfg")
    ap.add_argument("--tags", default=TAGS, help="the caption / style tags")
    ap.add_argument("--lyrics", default="", help="lyrics (empty = instrumental)")
    ap.add_argument("--save", default="", help="folder to download each render into")
    ap.add_argument("--label", default="", help="prefix for the saved filenames")
    # 🎚 the XL knobs the research says actually matter (see CHANGELOG v1.277.18)
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--shift", type=float, default=3.0,
                    help="ModelSamplingAuraFlow shift; 0 = bypass the node")
    ap.add_argument("--bpm", type=int, default=0,
                    help="set the METADATA bpm (upstream: keep tempo OUT of the caption)")
    ap.add_argument("--no-codes", action="store_true",
                    help="generate_audio_codes=false (faster; some report better)")
    ap.add_argument("--apg", default="",
                    help="eta,norm_threshold,momentum — e.g. 1.05,1.3,0.0")
    ap.add_argument("--flac", action="store_true",
                    help="save FLAC: judge a MIX on a lossless file, not V0 mp3")
    ap.add_argument("--seed", type=int, default=4242)
    a = ap.parse_args()

    hosts = ([h.strip() for h in a.hosts.split(",") if h.strip()]
             or [h["host"] for h in _hosts()
                 if _engine_status(h["host"]).get(a.engine, {}).get("ready")])
    if not hosts:
        print(f"no box is ready for {a.engine}")
        return 1
    print(f"🎚 {a.engine} · {a.seconds:.0f}s · {a.repeat}x per box · {len(hosts)} box(es)")
    if a.steps or a.cfg:
        print(f"   KSampler override: steps={a.steps or 'default'} cfg={a.cfg or 'default'}")
    rows = []
    for host in hosts:
        for n in range(a.repeat):
            # ⚠ same SEED across runs when a listening pack is being made —
            # comparing two settings on two different seeds compares seeds.
            seed = a.seed if a.save else a.seed + n
            if a.engine == "minimax3":
                g = _mm3_graph(host, a.tags, a.lyrics, a.seconds, seed,
                               "RBMN-BENCH/mm3")
                ks = "7"
            elif a.engine in ACE_XL:
                # the QUALITY lane: its own KSampler node id, and OUR
                # defaults (50 steps, cfg 3 — not ComfyUI's 7/6) unless
                # overridden on the command line
                apg = None
                if a.apg:
                    e, nt, mo = (list(map(float, a.apg.split(","))) + [0, 0])[:3]
                    apg = {"eta": e, "norm": nt, "momentum": mo}
                g = _ace_xl_graph(host, a.engine, a.tags, a.lyrics, a.seconds,
                                  seed, a.bpm, "", "en", "RBMN-BENCH/acexl",
                                  sampler=a.sampler, scheduler=a.scheduler,
                                  shift=a.shift,
                                  audio_codes=(False if a.no_codes else None),
                                  apg=apg, flac=a.flac)
                ks = "8"
            else:
                g = _ace_graph(host, a.tags, a.lyrics, a.seconds, seed,
                               a.bpm, "", "en", "RBMN-BENCH/ace", flac=a.flac)
                ks = "6"
            if a.steps:
                g[ks]["inputs"]["steps"] = a.steps
            if a.cfg:
                g[ks]["inputs"]["cfg"] = a.cfg
            tag = a.label or (f"{a.engine}_s{a.steps or 'def'}_c{a.cfg or 'def'}")
            rows.append(one(host, g, f"{tag} run {n + 1}"
                            + (" (cold)" if not n else " (warm)"), a.save))

    print("\nsummary")
    cold = [r for r in rows if r["label"].endswith("(cold)")]
    warm = [r for r in rows if r["label"].endswith("(warm)")]
    for name, grp in (("cold", cold), ("warm", warm)):
        if not grp:
            continue
        print(f"  {name:<5} wall avg {sum(r['wall'] for r in grp) / len(grp):6.1f}s"
              f"   exec avg {sum(r['exec'] for r in grp) / len(grp):6.1f}s")
    if cold and warm:
        d = (sum(r["wall"] for r in cold) / len(cold)
             - sum(r["wall"] for r in warm) / len(warm))
        print(f"  ⭐ the model load costs about {d:.0f}s per COLD render — "
              f"{'schedule cues back-to-back on one box' if d > 5 else 'negligible; the sampling IS the cost'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
