#!/usr/bin/env python3
"""diag_timeline.py — Narration timeline / alignment diagnostic (v3).

Auto-targets the MOST RECENTLY EDITED project and prints a full per-scene
alignment table (works for SRT cues AND Whisper word-gaps), bleed detection,
and a drift-growth check (does the boundary error grow toward the end?).
Writes ./timeline_diag.md (also prints). Stdlib only.

Usage:
    python tools/diag_timeline.py                 # most-recent project
    python tools/diag_timeline.py --project name  # a specific one
    python tools/diag_timeline.py --db PATH
"""
from __future__ import annotations
import argparse, json, re, shutil, sqlite3, subprocess, sys
from pathlib import Path


def find_db(explicit):
    cands = []
    if explicit: cands.append(Path(explicit).expanduser())
    cands.append(Path("~/RBMN-Projects/RBMN.db").expanduser())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.config import settings as _s
        cands.append(Path(str(_s.db_path)))
    except Exception:
        pass
    for c in cands:
        if c.exists(): return c
    return None


def jload(v):
    if v is None: return None
    if isinstance(v, (list, dict)): return v
    try: return json.loads(v)
    except Exception: return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--out", default="timeline_diag.md")
    args = ap.parse_args()
    db = find_db(args.db)
    if not db:
        print("ERROR: could not locate RBMN.db. Pass --db PATH."); return 2
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT id,name,mode,updated_at FROM projects ORDER BY updated_at DESC")
    projs = cur.fetchall()
    if not projs: print("no projects"); return 2
    L = ["# Timeline Diagnostic v3", f"DB: `{db}`", ""]

    # summary (recency-sorted)
    L.append("## Projects (most recently edited first)")
    L.append("| # | name | mode | scenes | dur | source | cues | updated |")
    L.append("|---|------|------|--------|-----|--------|------|---------|")
    def quick(pid):
        cur.execute("SELECT COUNT(*) c, MAX(end_time) d FROM scenes WHERE project_id=?", (pid,))
        r = cur.fetchone()
        cur.execute("SELECT words FROM lyrics WHERE project_id=?", (pid,))
        ly = cur.fetchone(); w = jload(ly["words"]) if ly else []
        w = w or []
        nb = sum(1 for x in w if isinstance(x, dict) and x.get("block") is not None)
        src = "srt" if nb else ("whisper" if w else "none")
        cu = len({int(x["block"]) for x in w if isinstance(x, dict) and x.get("block") is not None})
        return r["c"], (r["d"] or 0), src, cu
    for i, p in enumerate(projs):
        c, d, src, cu = quick(p["id"])
        L.append(f"| {i} | {str(p['name'])[:30]} | {p['mode']} | {c} | {d:.0f} | {src} | {cu} | {str(p['updated_at'])[:19]} |")
    L.append("")

    # target = explicit match or most-recent
    target = projs[0]
    if args.project:
        q = args.project.lower().replace("-", "").replace("_", "")
        for p in projs:
            if q in str(p["name"]).lower().replace("-","").replace("_","") or q in str(p["id"]).lower().replace("-",""):
                target = p; break
    pid = target["id"]
    cur.execute("SELECT order_index,name,start_time,end_time,parameters FROM scenes WHERE project_id=? ORDER BY order_index", (pid,))
    scenes = cur.fetchall()
    cur.execute("SELECT words FROM lyrics WHERE project_id=?", (pid,))
    ly = cur.fetchone(); words = (jload(ly["words"]) if ly else []) or []
    blocked = [w for w in words if isinstance(w, dict) and w.get("block") is not None]
    src = "srt" if blocked else ("whisper" if words else "none")

    L.append(f"## TARGET: {target['name']}  (mode={target['mode']}, updated {str(target['updated_at'])[:19]})")
    L.append(f"scenes={len(scenes)} · words={len(words)} · source={src.upper()} · cues={len({int(w['block']) for w in blocked}) if blocked else 0}")
    # Project settings that affect SRT re-anchor
    cur.execute("SELECT settings FROM projects WHERE id=?", (pid,))
    _prow = cur.fetchone()
    _psettings = jload(_prow["settings"]) if _prow else {}
    _psettings = _psettings or {}
    _dw = _psettings.get("disable_whisper", False)
    _wend = max((float(w.get("end",0) or 0) for w in words), default=0.0)
    L.append(f"**disable_whisper = {_dw}**  ·  last-word time = {_wend:.2f}s  "
             + ("(SRT re-anchor is SKIPPED when disable_whisper=True → SRT's drifting times are kept)" if _dw
                else "(re-anchor allowed; if last-word ≈ original SRT 775.47s it didn't run, if it moved toward audio length it did)"))

    # Build "anchor" segments: list of (start,end,text) spoken spans
    spans = []
    if src == "srt":
        byb = {}
        for w in words:
            b = w.get("block")
            if b is None: continue
            byb.setdefault(int(b), []).append(w)
        for b in sorted(byb):
            ws = byb[b]
            spans.append((min(float(x.get("start",0) or 0) for x in ws),
                          max(float(x.get("end",0) or 0) for x in ws),
                          " ".join(str(x.get("word","")).strip() for x in ws)))
    elif src == "whisper":
        # group whisper words into "spoken spans" split on gaps > 0.4s
        cur_ws = []
        for i, w in enumerate(words):
            cur_ws.append(w)
            nxt = words[i+1].get("start", 0) if i+1 < len(words) else None
            gap = (nxt - w.get("end", 0)) if nxt is not None else 99
            if gap > 0.4:
                spans.append((float(cur_ws[0].get("start",0) or 0), float(cur_ws[-1].get("end",0) or 0),
                              " ".join(str(x.get("word","")).strip() for x in cur_ws)))
                cur_ws = []
        if cur_ws:
            spans.append((float(cur_ws[0].get("start",0) or 0), float(cur_ws[-1].get("end",0) or 0),
                          " ".join(str(x.get("word","")).strip() for x in cur_ws)))

    # gap midpoints between spoken spans
    gap_mids = []
    for i in range(len(spans)-1):
        gap_mids.append(((spans[i][1]+spans[i+1][0])/2.0, spans[i+1][0]-spans[i][1]))

    L.append("")
    L.append("ALIGNMENT (does each scene END sit in a silence, or mid-speech = BLEED?):")
    L.append("| scene | start | end | dur | end inside speech? (BLEED) | nearest gap-mid | Δ |")
    L.append("|-------|-------|-----|-----|----------------------------|-----------------|---|")
    deltas = []; bleeds = 0
    for sc in scenes:
        st = float(sc["start_time"]); en = float(sc["end_time"])
        inside = ""
        for (s,e,t) in spans:
            if s + 0.05 < en < e - 0.05:
                inside = f"'{t[:24]}'"; bleeds += 1; break
        nm = ""; d = ""
        if gap_mids:
            best = min(gap_mids, key=lambda g: abs(g[0]-en))
            nm = f"{best[0]:.2f}"; dd = en - best[0]; d = f"{dd:+.2f}"
            if not inside:  # only count Δ for non-final, real boundaries
                deltas.append(abs(dd))
        L.append(f"| {sc['order_index']} | {st:.2f} | {en:.2f} | {en-st:.2f} | {inside or 'no'} | {nm} | {d} |")
    L.append("")
    # drift growth: compare first third vs last third (excluding the final scene's outro Δ)
    core = deltas[:-1] if deltas else []
    if len(core) >= 6:
        n3 = max(1, len(core)//3)
        first = sum(core[:n3])/n3; last = sum(core[-n3:])/n3
        L.append(f"SUMMARY: bleed scenes={bleeds}/{len(scenes)} · avg |Δ| first-third={first:.2f}s last-third={last:.2f}s "
                 + ("→ DRIFT GROWS toward end" if last > first*1.8 + 0.2 else "→ no growth (boundaries stable)"))
    else:
        L.append(f"SUMMARY: bleed scenes={bleeds}/{len(scenes)} · (too few boundaries for drift-growth trend)")
    L.append("")
    L.append("first 14 spoken spans (start-end text): ")
    for (s,e,t) in spans[:14]:
        L.append(f"  {s:.1f}-{e:.1f}  {t[:50]}")

    # ── AUDIO REALITY CHECK: does the SRT/scene clock match the real audio? ──
    L.append("")
    L.append("## AUDIO REALITY CHECK")
    db_dir = Path(str(db)).parent
    import os
    app_pdir = None
    try:
        from backend.config import settings as _appset
        app_pdir = Path(str(_appset.project_dir))
    except Exception:
        app_pdir = None
    # The RUNNING app uses AppSettings.project_dir (DB override) if set —
    # that's where the real files live.  The config default may differ.
    db_override = None
    try:
        cur.execute("SELECT project_dir FROM app_settings LIMIT 1")
        _r = cur.fetchone()
        if _r and _r["project_dir"]:
            db_override = Path(str(_r["project_dir"]))
    except Exception as _e:
        L.append(f"(could not read app_settings.project_dir: {_e})")
    L.append(f"config default project_dir = `{app_pdir}`")
    L.append(f"**DB-override project_dir (what the running app uses) = `{db_override}`**")
    L.append(f"db file is in = `{db_dir}`")
    # Prefer the DB override as the search/probe root.
    real_root = db_override or app_pdir or db_dir

    def _find_all(fname, roots, cap=8):
        hits = []
        seen = set()
        rs = []
        for r in roots:
            if r and Path(r).exists() and str(r) not in seen:
                seen.add(str(r)); rs.append(Path(r))
        for root in rs:
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                if fname in filenames:
                    hits.append(Path(dirpath) / fname)
                    if len(hits) >= cap:
                        return hits
        return hits

    def _find_file(fname):
        roots = [db_override, app_pdir, db_dir, (Path(db_dir).parent if db_dir else None)]
        hits = _find_all(fname, roots)
        for h in hits:
            L.append(f"  found-on-disk: {h}")
        if not hits:
            L.append(f"  NOT FOUND anywhere under: {[str(r) for r in roots if r]}")
        return hits[0] if hits else None
    # Dump ALL assets for this project so we can see the real type/path,
    # then locate the audio file (DB type values vary; also search disk).
    cur.execute("SELECT filename, rel_path, asset_type, duration_sec FROM assets WHERE project_id=?", (pid,))
    all_assets = cur.fetchall()
    L.append(f"assets in DB for this project: {len(all_assets)}")
    for a in all_assets[:12]:
        L.append(f"  - type={a['asset_type']} dur={a['duration_sec']} file={a['filename']} rel={a['rel_path']}")
    arow = None
    for a in all_assets:
        t = str(a["asset_type"]).lower()
        if any(k in t for k in ("music","narration","audio")) or str(a["filename"] or "").lower().endswith((".wav",".mp3",".m4a",".flac",".ogg")):
            arow = a; break
    scene_max = max((float(x["end_time"]) for x in scenes), default=0.0)
    word_end = max((float(w.get("end",0) or 0) for w in words), default=0.0)
    word_start0 = min((float(w.get("start",0) or 0) for w in words), default=0.0)
    if not arow:
        L.append("No audio asset row found (music/narration/audio) — cannot probe audio.")
    else:
        rel = (arow["rel_path"] or "").replace("\\", "/")
        fn = arow["filename"] or ""
        dashed = pid
        if len(pid) == 32 and "-" not in pid:
            dashed = f"{pid[0:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"
        _roots = [r for r in (db_override, app_pdir, db_dir) if r]
        cands = []
        for _rt in _roots:
            cands += [_rt / rel, _rt / dashed / rel, _rt / pid / rel,
                      _rt / dashed / fn, _rt / pid / fn]
        apath = next((c for c in cands if c.exists()), None)
        if apath is None and fn:
            apath = _find_file(fn)
        L.append(f"audio asset: {arow['filename']}  · DB duration_sec={arow['duration_sec']}")
        L.append(f"scene timeline max = {scene_max:.2f}s · SRT/word span = {word_start0:.2f}..{word_end:.2f}s")
        if apath is None:
            L.append(f"audio file not found at: {cands[0]}  (checked {len(cands)} paths)")
        else:
            ffprobe = shutil.which("ffprobe"); ffmpeg = shutil.which("ffmpeg")
            real_dur = None
            if ffprobe:
                try:
                    r = subprocess.run([ffprobe,"-v","error","-show_entries","format=duration","-of","default=nk=1:nw=1",str(apath)],capture_output=True,text=True,timeout=60)
                    real_dur = float(r.stdout.strip())
                except Exception as e:
                    L.append(f"ffprobe failed: {e}")
            if real_dur:
                L.append(f"**ACTUAL audio file duration = {real_dur:.2f}s**")
                if abs(real_dur - scene_max) > 2.0:
                    L.append(f"⚠️ SCALE MISMATCH: scene timeline ({scene_max:.2f}s) vs real audio ({real_dur:.2f}s) differ by {scene_max-real_dur:+.2f}s — the timeline is drawn on a different clock than the audio (drift grows toward the end).")
                else:
                    L.append(f"timeline length matches audio (±{scene_max-real_dur:+.2f}s).")
            # silencedetect → speech onsets vs SRT cue starts
            if ffmpeg and src == "srt":
                try:
                    r = subprocess.run([ffmpeg,"-i",str(apath),"-af","silencedetect=noise=-30dB:d=0.35","-f","null","-"],capture_output=True,text=True,timeout=180)
                    log = r.stderr
                    onsets = [0.0] + [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", log)]
                    cue_starts = [s0 for (s0,_e,_t) in spans]
                    L.append(f"detected {len(onsets)} speech onsets in audio vs {len(cue_starts)} SRT cue starts")
                    # compare aligned by nearest index over the shorter list
                    n = min(len(onsets), len(cue_starts))
                    if n >= 10:
                        def off(i): 
                            # nearest detected onset to cue start i
                            cs = cue_starts[i]; best=min(onsets,key=lambda o:abs(o-cs)); return best-cs
                        import statistics
                        early = statistics.mean(abs(off(i)) for i in range(3,8))
                        mid = statistics.mean(abs(off(i)) for i in range(n//2-2,n//2+3))
                        late = statistics.mean(abs(off(i)) for i in range(n-8,n-3))
                        L.append(f"|cue-start vs nearest audio speech onset| — early≈{early:.2f}s, mid≈{mid:.2f}s, late≈{late:.2f}s")
                        if late > early*1.8 + 0.3 or late > 1.0:
                            L.append("⚠️ SRT-vs-AUDIO DRIFT: the SRT cue times increasingly disagree with where speech actually is in the audio (classic ElevenLabs long-file timestamp drift). Scenes align to the SRT, so playback drifts from the audio toward the end.")
                        else:
                            L.append("SRT cue starts track the audio's actual speech onsets (no growing drift).")
                except Exception as e:
                    L.append(f"silencedetect failed: {e}")
            elif not ffmpeg:
                L.append("ffmpeg not on PATH — skipped speech-onset comparison.")

    # Sanity: do the project's IMAGE files exist where the DB expects?  If
    # images resolve but audio doesn't, only the audio was displaced.  If
    # NOTHING resolves, the whole project folder moved (e.g. the old broken
    # change_project_dir).
    try:
        cur.execute("SELECT filename, rel_path FROM assets WHERE project_id=? AND asset_type IN ('character','generated_image') LIMIT 1", (pid,))
        _img = cur.fetchone()
        if _img:
            _irel = (_img["rel_path"] or "").replace("\\","/")
            _ic = [db_dir / _irel, db_dir / pid / _irel]
            _ihit = next((c for c in _ic if c.exists()), None)
            L.append(f"image-asset on disk? {'YES: '+str(_ihit) if _ihit else 'NO ('+(_img['filename'] or '')+')'}")
    except Exception as _e:
        L.append(f"(image check failed: {_e})")

    out = "\n".join(L) + "\n"
    Path(args.out).write_text(out, encoding="utf-8")
    print(out); print(f"[written to {Path(args.out).resolve()}]")
    con.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
