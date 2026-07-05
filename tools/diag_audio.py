"""Audit a project's ENTIRE audio chain: master, per-scene clips, content.

Usage:
    python tools/diag_audio.py "<project name fragment or id>"
    python tools/diag_audio.py "<project>" --db "C:\\path\\to\\RBMN.db"

Prints, with zero guessing:
  - the project's master audio asset(s): file, size, ffprobe codec/duration
  - every scene: start/end, audio_clip_path, file exists?, codec, duration,
    and a CONTENT FINGERPRINT (md5 of the first 2s of decoded PCM)
  - verdicts: are clips missing? wrong codec? DO MULTIPLE SCENES SHARE THE
    SAME AUDIO CONTENT? (the "scene 1 over and over" bug shows up as one
    fingerprint repeated across scenes)

Requires ffmpeg/ffprobe on PATH. Read-only - changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path


def ffprobe(path: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,sample_rate,channels:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        return {
            "codec": st.get("codec_name", "?"),
            "sr": st.get("sample_rate", "?"),
            "ch": st.get("channels", "?"),
            "dur": round(float((d.get("format") or {}).get("duration", 0) or 0), 2),
        }
    except Exception as e:
        return {"codec": f"probe-failed:{e}", "sr": "?", "ch": "?", "dur": 0}


def content_fp(path: Path, seconds: float = 2.0) -> str:
    """md5 of the first N seconds DECODED to raw PCM - container-agnostic,
    so identical audio content gives identical fingerprints."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-t", str(seconds),
             "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
            capture_output=True, timeout=30,
        )
        if not r.stdout:
            return "decode-failed"
        return hashlib.md5(r.stdout).hexdigest()[:10]
    except Exception as e:
        return f"fp-failed:{e}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    needle = sys.argv[1].lower()
    db_path = None
    if "--db" in sys.argv:
        db_path = Path(sys.argv[sys.argv.index("--db") + 1])
    if db_path is None:
        db_path = Path.home() / "RBMN-Projects" / "RBMN.db"
    if not db_path.exists():
        print(f"DB not found at {db_path} - pass it with --db")
        return 2
    project_root = db_path.parent

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # The app supports a project_dir OVERRIDE stored in app_settings — the
    # DB file and the project FILES can live in different roots (e.g. after
    # a failed/partial "move project folder"). Honor it.
    override_root = None
    try:
        row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
        if row and row["project_dir"]:
            cand = Path(row["project_dir"])
            if cand.exists():
                override_root = cand
    except Exception:
        pass
    print(f"DB: {db_path}\nDB-parent root: {project_root}")
    print(f"app_settings.project_dir override: {override_root or '(none)'}")
    roots = [r for r in [override_root, project_root] if r]
    print(f"Searching roots: {[str(r) for r in roots]}\n")
    rows = con.execute("SELECT id, name, mode FROM projects").fetchall()
    matches = [r for r in rows if needle in (r["name"] or "").lower() or needle in str(r["id"]).lower()]
    if not matches:
        print(f"No project matching {needle!r}. Projects:")
        for r in rows:
            print(f"  {r['id']}  {r['name']}")
        return 2
    if len(matches) > 1:
        print("Multiple matches - be more specific:")
        for r in matches:
            print(f"  {r['id']}  {r['name']}")
        return 2
    proj = matches[0]
    pid_hex = str(proj["id"])
    _h = pid_hex.replace("-", "")
    pid_dashed = f"{_h[0:8]}-{_h[8:12]}-{_h[12:16]}-{_h[16:20]}-{_h[20:32]}" if len(_h) == 32 else pid_hex
    proj_dir = None
    for _root in roots:
        for _name in (pid_dashed, pid_hex):
            cand = _root / _name
            if cand.exists():
                proj_dir = cand
                project_root = _root
                break
        if proj_dir:
            break
    if proj_dir is None:
        proj_dir = (roots[0] if roots else project_root) / pid_dashed
        print(f"!! project folder not found under any root — checked "
              f"{[str(r / n) for r in roots for n in (pid_dashed, pid_hex)]}")
    else:
        try:
            _top = sorted(p.name for p in proj_dir.iterdir())[:12]
            print(f"project folder contents: {_top}")
            _ac = proj_dir / "audio_clips"
            if _ac.exists():
                _clips = sorted(p.name for p in _ac.iterdir())
                print(f"audio_clips/ has {len(_clips)} file(s): {_clips[:5]}{' ...' if len(_clips) > 5 else ''}")
            else:
                print("audio_clips/ folder DOES NOT EXIST")
        except Exception as _ls_err:
            print(f"folder listing failed: {_ls_err}")
    print(f"=== Project: {proj['name']} ({proj['mode']}) ===\nid={pid_hex}\ndir={proj_dir} exists={proj_dir.exists()}\n")

    try:
        srow = con.execute("SELECT settings FROM projects WHERE id=?", (proj["id"],)).fetchone()
        pset = json.loads(srow["settings"] or "{}")
        print(f"audio_source: {pset.get('audio_source')!r}   aaf_import: {json.dumps(pset.get('aaf_import')) if pset.get('aaf_import') else None}\n")
    except Exception as e:
        print(f"settings read failed: {e}\n")

    print("--- MASTER AUDIO ASSETS (type music, non-stem) ---")
    assets = con.execute(
        "SELECT id, filename, rel_path, file_size, created_at FROM assets "
        "WHERE project_id=? AND asset_type IN ('music','MUSIC') ORDER BY created_at",
        (proj["id"],),
    ).fetchall()
    masters = [a for a in assets if "stems/" not in (a["rel_path"] or "")]
    if not masters:
        print("  NONE - no master audio. That alone breaks slicing/playback.")
    for a in masters:
        f = proj_dir / (a["rel_path"] or "")
        info = ffprobe(f) if f.exists() else {}
        print(f"  {a['rel_path']}  size={a['file_size']}  exists={f.exists()}  "
              f"{('codec=' + str(info.get('codec')) + ' dur=' + str(info.get('dur')) + 's') if f.exists() else ''}")

    print("\n--- SCENES / CLIPS ---")
    scenes = con.execute(
        "SELECT order_index, name, start_time, end_time, parameters FROM scenes "
        "WHERE project_id=? ORDER BY order_index", (proj["id"],),
    ).fetchall()
    fps: list = []
    missing = wrong_codec = 0
    for sc in scenes:
        try:
            params = json.loads(sc["parameters"] or "{}")
        except Exception:
            params = {}
        clip_rel = params.get("audio_clip_path") or ""
        line = f"  #{sc['order_index']:>3} {float(sc['start_time'] or 0):8.2f}-{float(sc['end_time'] or 0):8.2f}s"
        if not clip_rel:
            line += "  clip=NONE"
            missing += 1
        else:
            f = project_root / clip_rel
            if not f.exists():
                f2 = proj_dir / clip_rel
                f = f2 if f2.exists() else f
            if not f.exists():
                line += f"  clip=MISSING-FILE ({clip_rel})"
                missing += 1
            else:
                info = ffprobe(f)
                fp = content_fp(f)
                fps.append((sc["order_index"], fp))
                if str(info.get("codec")) not in ("pcm_s16le", "pcm_s24le", "pcm_f32le"):
                    wrong_codec += 1
                line += f"  codec={info['codec']} dur={info['dur']}s fp={fp}  ({Path(clip_rel).name})"
        print(line)

    print("\n=== VERDICT ===")
    if missing:
        print(f"  X {missing}/{len(scenes)} scenes have NO usable clip -> slicing didn't run/persist for them.")
    if wrong_codec:
        print(f"  X {wrong_codec} clips are NOT real PCM WAV (e.g. mp3-in-wav) -> browsers/ComfyUI mis-decode them.")
    if fps:
        counts = Counter(fp for _i, fp in fps)
        top_fp, top_n = counts.most_common(1)[0]
        uniq = len(counts)
        print(f"  content fingerprints: {uniq} unique across {len(fps)} clips "
              f"(most common appears {top_n}x)")
        if uniq == 1 and len(fps) > 1:
            print("  XX ALL CLIPS HAVE IDENTICAL AUDIO CONTENT - the 'scene 1 over and over' bug. "
                  "Re-import the AAF on current code (restarted backend).")
        elif top_n > max(2, len(fps) // 4):
            print(f"  X {top_n} clips share identical content (fp={top_fp}) - partial duplication.")
        else:
            print("  OK clips carry distinct audio content, as expected.")
    if not missing and not wrong_codec and fps and len(Counter(fp for _i, fp in fps)) > 1:
        print("  OK audio chain looks HEALTHY - if the UI still misbehaves it is a frontend/cache issue "
              "(hard-refresh with Ctrl+Shift+R and check the browser console).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
