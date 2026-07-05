"""rbmn.py — unified troubleshooting / debugging CLI for RBMN-Storyboard_App.

    python tools/rbmn.py <command> [args] [--db PATH] [--port N]

EVERY command mirrors its full output to  diagnostics/latest_<command>.txt
(plus a timestamped copy) inside the repo, so Claude can read results
directly from the mounted folder without copy/paste.

INSPECTION (read-only, DB + files, backend NOT required):
  projects                       list every project (id, name, mode, scenes)
  project  <match>               deep snapshot: settings/flags, chapters, scenes
  prompts  <match> [scene_idx]   stored vs SUBMITTED prompts, refs, modes per scene
  jobs     <match> [N]           last N jobs: status, workflow_type, errors
  audio    <match>               full audio-chain audit (master, clips, fingerprints)
  timeline <match>               boundary audit (wraps tools/diag_timeline.py)
  chapters <match>               chapter audit (wraps tools/diag_chapters.py)
  general                        global snapshot (wraps tools/diag.py)
  db                             DB health: counts, orphans, WAL, key settings
  aaf      <file.aaf>            inspect an AAF file (wraps tools/diag_aaf.py)
  media    <file>                ffprobe + decoded-content fingerprint
  logs     [N] [grep]            tail logs/rbmn.log (last N lines, optional filter)

LIVE BACKEND (app must be running; port auto-read from app_settings.app_port):
  health                         GET /api/health
  api <METHOD> <path> [json]     raw API call, e.g.:
                                   api GET /api/projects
                                   api POST /api/projects/<id>/timeline/slice-audio
  slice      <match>             re-slice per-scene audio clips
  detach-aaf <match>             clear the AAF-authoritative flag

<match> = project name fragment OR id fragment (must be unambiguous).
Full documentation: docs/CLI_TOOLS.md
"""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIAG_DIR = REPO / "diagnostics"
TOOLS = REPO / "tools"


# ── output tee: everything printed also lands in diagnostics/ ────────────
class Tee:
    def __init__(self, cmd: str):
        self.cmd = cmd
        self.buf = io.StringIO()

    def p(self, *args):
        line = " ".join(str(a) for a in args)
        print(line)
        self.buf.write(line + "\n")

    def flush_to_disk(self):
        DIAG_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        body = f"# rbmn {self.cmd} @ {datetime.now().isoformat()}\n" + self.buf.getvalue()
        (DIAG_DIR / f"latest_{self.cmd}.txt").write_text(body, encoding="utf-8")
        (DIAG_DIR / f"{ts}_{self.cmd}.txt").write_text(body, encoding="utf-8")
        print(f"\n[report saved: diagnostics/latest_{self.cmd}.txt]")


# ── shared helpers ────────────────────────────────────────────────────────
def find_db(argv) -> Path:
    if "--db" in argv:
        return Path(argv[argv.index("--db") + 1])
    return Path.home() / "RBMN-Projects" / "RBMN.db"


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path} (use --db PATH)")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def project_roots(con, db_path: Path) -> list:
    roots = []
    try:
        row = con.execute("SELECT project_dir FROM app_settings WHERE id=1").fetchone()
        if row and row["project_dir"] and Path(row["project_dir"]).exists():
            roots.append(Path(row["project_dir"]))
    except Exception:
        pass
    if db_path.parent not in roots:
        roots.append(db_path.parent)
    return roots


def dashed(pid: str) -> str:
    h = str(pid).replace("-", "")
    if len(h) != 32:
        return str(pid)
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def resolve_project(con, needle: str):
    rows = con.execute("SELECT id, name, mode, settings FROM projects").fetchall()
    n = needle.lower()
    m = [r for r in rows if n in (r["name"] or "").lower() or n in str(r["id"]).lower()]
    if len(m) == 1:
        return m[0]
    print(f"{'No' if not m else 'Multiple'} project(s) matching {needle!r}:")
    for r in (m or rows):
        print(f"  {r['id']}  {r['name']}")
    raise SystemExit(2)


def proj_dir_for(con, db_path: Path, pid: str) -> Path:
    for root in project_roots(con, db_path):
        for name in (dashed(pid), str(pid).replace("-", "")):
            c = root / name
            if c.exists():
                return c
    return project_roots(con, db_path)[0] / dashed(pid)


def get_port(con) -> int:
    try:
        row = con.execute("SELECT app_port FROM app_settings WHERE id=1").fetchone()
        return int(row["app_port"]) if row and row["app_port"] else 8899
    except Exception:
        return 8899


def api_call(method: str, path: str, port: int, payload=None, timeout: int = 300):
    import urllib.request
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = e.read().decode("utf-8", "replace")[:2000]
            except Exception:
                pass
        return getattr(e, "code", 0), f"{type(e).__name__}: {e} {detail}"


def run_wrapped(script: str, args: list, t: Tee):
    r = subprocess.run([sys.executable, str(TOOLS / script), *args],
                       capture_output=True, text=True, cwd=str(REPO))
    t.p(r.stdout)
    if r.stderr.strip():
        t.p("[stderr]", r.stderr[-2000:])


def ffprobe_info(path: Path) -> dict:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_name,sample_rate,channels:format=duration,size",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        fmt = d.get("format") or {}
        return {"codec": st.get("codec_name"), "sr": st.get("sample_rate"),
                "ch": st.get("channels"), "dur": round(float(fmt.get("duration", 0) or 0), 2),
                "size": fmt.get("size")}
    except Exception as e:
        return {"codec": f"probe-failed: {e}"}


# ── commands ──────────────────────────────────────────────────────────────
def cmd_projects(argv):
    t = Tee("projects")
    db = find_db(argv); con = open_db(db)
    t.p(f"DB: {db}")
    rows = con.execute(
        "SELECT p.id, p.name, p.mode, p.settings,"
        " (SELECT COUNT(*) FROM scenes s WHERE s.project_id=p.id) AS nsc,"
        " (SELECT COUNT(*) FROM chapters c WHERE c.project_id=p.id) AS nch"
        " FROM projects p ORDER BY p.name").fetchall()
    for r in rows:
        try:
            ps = json.loads(r["settings"] or "{}")
        except Exception:
            ps = {}
        flags = []
        if ps.get("audio_source") == "aaf": flags.append("AAF")
        if ps.get("two_pass_enabled") or ps.get("two_pass"): flags.append("2PASS")
        for k, tag in (("json_prompt_mode", "IDEO"), ("scene_intent_mode", "INTENT"), ("video_json_mode", "VJSON")):
            if ps.get(k): flags.append(tag)
        t.p(f"  {r['id']}  [{r['mode']:>16}] scenes={r['nsc']:<3} chapters={r['nch']:<3} "
            f"{('[' + ','.join(flags) + ']') if flags else '':<22} {r['name']}")
    t.p(f"\n{len(rows)} project(s)")
    t.flush_to_disk()


def cmd_project(argv):
    t = Tee("project")
    db = find_db(argv); con = open_db(db)
    proj = resolve_project(con, argv[0])
    pid = proj["id"]
    pdir = proj_dir_for(con, db, pid)
    try:
        ps = json.loads(proj["settings"] or "{}")
    except Exception:
        ps = {}
    t.p(f"=== {proj['name']} ({proj['mode']}) ===\nid={pid}\ndir={pdir} exists={pdir.exists()}\n")
    keys = ["audio_source", "aaf_import", "disable_whisper", "json_prompt_mode", "scene_intent_mode",
            "video_json_mode", "global_color_override", "image_direction", "enable_model_audio",
            "use_transition_lora", "scenes_locked", "image_resolution_width", "video_resolution_width"]
    t.p("-- settings (selected) --")
    for k in keys:
        if k in ps:
            t.p(f"  {k}: {json.dumps(ps[k])[:120]}")
    chars = ps.get("characters") or []
    t.p(f"  characters: {len(chars)} -> {[c.get('name') for c in chars]}")
    t.p("\n-- chapters --")
    for ch in con.execute("SELECT depth, order_index, name, source, start_time, end_time FROM chapters "
                          "WHERE project_id=? ORDER BY depth, order_index", (pid,)):
        t.p(f"  {'  ' * ch['depth']}[{ch['source'] or 'auto'}] {ch['name']}  "
            f"{ch['start_time']:.1f}-{ch['end_time']:.1f}s")
    t.p("\n-- scenes --")
    for sc in con.execute("SELECT order_index, name, start_time, end_time, prompt, parameters "
                          "FROM scenes WHERE project_id=? ORDER BY order_index", (pid,)):
        try:
            sp = json.loads(sc["parameters"] or "{}")
        except Exception:
            sp = {}
        marks = "".join([
            "F" if sp.get("chosen_image_path") else "-",
            "L" if sp.get("chosen_last_frame_path") else "-",
            "V" if sp.get("chosen_video_path") else "-",
            "A" if sp.get("audio_clip_path") else "-",
            "S" if sp.get("flow_idea") else "-",
        ])
        refs = (sp.get("image_refs_first") or {}).get("characterIndices")
        t.p(f"  #{sc['order_index']:>3} {sc['start_time']:8.2f}-{sc['end_time']:8.2f}s "
            f"[{marks}] refs={refs if refs is not None else '(auto)'} "
            f"prompt={len(sc['prompt'] or '')}ch  {sc['name'][:40]}")
    t.p("\n  marks: F=first frame, L=last frame, V=video, A=audio clip, S=story flow")
    t.flush_to_disk()


def cmd_prompts(argv):
    t = Tee("prompts")
    db = find_db(argv); con = open_db(db)
    proj = resolve_project(con, argv[0])
    only = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else None
    for sc in con.execute("SELECT order_index, name, prompt, parameters FROM scenes "
                          "WHERE project_id=? ORDER BY order_index", (proj["id"],)):
        if only is not None and sc["order_index"] != only:
            continue
        try:
            sp = json.loads(sc["parameters"] or "{}")
        except Exception:
            sp = {}
        t.p(f"\n===== scene #{sc['order_index']} {sc['name'][:50]} =====")
        for label, val in [
            ("prompt (stored FF)", sc["prompt"]),
            ("submitted_image_prompt", sp.get("submitted_image_prompt")),
            ("last_frame_prompt", sp.get("last_frame_prompt")),
            ("submitted_last_frame_prompt", sp.get("submitted_last_frame_prompt")),
            ("video_prompt", sp.get("video_prompt")),
            ("submitted_video_prompt", sp.get("submitted_video_prompt")),
            ("flow_idea", sp.get("flow_idea")),
            ("llm_instruction_image", sp.get("llm_instruction_image")),
            ("llm_instruction_video", sp.get("llm_instruction_video")),
        ]:
            if val:
                t.p(f"--- {label} ({len(str(val))}ch):\n{str(val)[:600]}")
        for flag in ("json_prompt_mode", "scene_intent_mode", "video_json_mode",
                     "image_refs_first", "image_refs_first_manual", "two_pass_enabled"):
            if flag in sp:
                t.p(f"  {flag} = {json.dumps(sp[flag])[:200]}")
    t.flush_to_disk()


def cmd_jobs(argv):
    t = Tee("jobs")
    db = find_db(argv); con = open_db(db)
    proj = resolve_project(con, argv[0])
    n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 25
    scene_names = {str(s["id"]): f"#{s['order_index']} {(s['name'] or '')[:18]}" for s in
                   con.execute("SELECT id, order_index, name FROM scenes WHERE project_id=?", (proj["id"],))}
    rows = con.execute("SELECT id, scene_id, job_type, status, error, created_at, parameters FROM jobs "
                       "WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (proj["id"], n)).fetchall()
    for r in rows:
        try:
            wp = json.loads(r["parameters"] or "{}")
        except Exception:
            wp = {}
        extra = wp.get("frame_type") or ""
        if wp.get("two_pass_phase"):
            extra += f"/{wp['two_pass_phase']}"
        elif wp.get("two_pass"):
            extra += "/2pass"
        t.p(f"  {r['created_at']} [{r['status']:>9}] {r['job_type']:<6} "
            f"wf={wp.get('workflow_type', '?'):<16} eff={wp.get('_effective_workflow_type', '-'):<3} "
            f"{extra:<12} {scene_names.get(str(r['scene_id']), '')}")
        if r["error"]:
            t.p(f"      ERROR: {str(r['error'])[:300]}")
    t.p(f"\n{len(rows)} job(s) shown")
    t.flush_to_disk()


def cmd_db(argv):
    t = Tee("db")
    db = find_db(argv); con = open_db(db)
    t.p(f"DB: {db}  size={db.stat().st_size / 1e6:.1f}MB")
    wal = db.with_name(db.name + "-wal")
    t.p(f"WAL: {'%.1fMB' % (wal.stat().st_size / 1e6) if wal.exists() else 'none'}")
    for tbl in ("projects", "scenes", "chapters", "assets", "jobs", "lyrics", "batch_runs"):
        try:
            c = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            t.p(f"  {tbl:<12} {c}")
        except Exception as e:
            t.p(f"  {tbl:<12} ERR {e}")
    stale = con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running','PENDING','RUNNING')").fetchone()[0]
    t.p(f"  pending/running jobs: {stale}")
    try:
        row = dict(con.execute("SELECT * FROM app_settings WHERE id=1").fetchone())
        for k in ("project_dir", "app_port", "image_model_type", "single_image_generator",
                  "video_model_type", "llm_provider", "restrict_explicit_content",
                  "krea2_sfw_mode", "vision_enabled"):
            if k in row:
                t.p(f"  app_settings.{k} = {row[k]}")
    except Exception as e:
        t.p(f"  app_settings read failed: {e}")
    t.flush_to_disk()


def cmd_media(argv):
    t = Tee("media")
    f = Path(argv[0])
    t.p(f"file: {f} exists={f.exists()}")
    if f.exists():
        t.p(json.dumps(ffprobe_info(f), indent=1))
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-t", "2",
                            "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                           capture_output=True, timeout=60)
        import hashlib
        t.p(f"content fingerprint (first 2s): {hashlib.md5(r.stdout).hexdigest()[:10] if r.stdout else 'decode-failed'}")
    t.flush_to_disk()


def cmd_logs(argv):
    t = Tee("logs")
    n = int(argv[0]) if argv and argv[0].isdigit() else 200
    needle = argv[1] if len(argv) > 1 else (argv[0] if argv and not argv[0].isdigit() else None)
    log = REPO / "logs" / "rbmn.log"
    if not log.exists():
        t.p(f"no log at {log}")
    else:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        if needle:
            lines = [l for l in lines if needle.lower() in l.lower()]
        for l in lines[-n:]:
            t.p(l)
        t.p(f"\n[{len(lines[-n:])} line(s){' matching ' + repr(needle) if needle else ''} from {log.name}]")
    t.flush_to_disk()


def cmd_health(argv):
    t = Tee("health")
    db = find_db(argv); con = open_db(db)
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else get_port(con)
    code, body = api_call("GET", "/api/health", port)
    t.p(f"GET http://127.0.0.1:{port}/api/health -> {code}")
    t.p(json.dumps(body, indent=1) if isinstance(body, (dict, list)) else str(body))
    t.flush_to_disk()


def cmd_api(argv):
    t = Tee("api")
    db = find_db(argv); con = open_db(db)
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else get_port(con)
    method, path = argv[0], argv[1]
    payload = json.loads(argv[2]) if len(argv) > 2 and not argv[2].startswith("--") else None
    code, body = api_call(method, path, port, payload)
    t.p(f"{method.upper()} {path} -> {code}")
    t.p(json.dumps(body, indent=1)[:8000] if isinstance(body, (dict, list)) else str(body)[:8000])
    t.flush_to_disk()


def _project_api(argv, cmd: str, method: str, subpath: str, payload=None):
    t = Tee(cmd)
    db = find_db(argv); con = open_db(db)
    proj = resolve_project(con, argv[0])
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else get_port(con)
    path = f"/api/projects/{dashed(proj['id'])}{subpath}"
    code, body = api_call(method, path, port, payload)
    t.p(f"{method} {path} -> {code}")
    t.p(json.dumps(body, indent=1)[:4000] if isinstance(body, (dict, list)) else str(body)[:4000])
    t.flush_to_disk()


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    wrapped = {
        "audio": ("diag_audio.py", 1), "timeline": ("diag_timeline.py", 1),
        "chapters": ("diag_chapters.py", 1), "general": ("diag.py", 0),
        "aaf": ("diag_aaf.py", 1),
    }
    if cmd in wrapped:
        script, min_args = wrapped[cmd]
        if len(rest) < min_args:
            print(f"usage: rbmn {cmd} <arg>")
            return 2
        t = Tee(cmd)
        run_wrapped(script, rest, t)
        t.flush_to_disk()
        return 0
    table = {
        "projects": cmd_projects, "project": cmd_project, "prompts": cmd_prompts,
        "jobs": cmd_jobs, "db": cmd_db, "media": cmd_media, "logs": cmd_logs,
        "health": cmd_health, "api": cmd_api,
        "slice": lambda a: _project_api(a, "slice", "POST", "/timeline/slice-audio"),
        "detach-aaf": lambda a: _project_api(a, "detach-aaf", "POST", "/timeline/detach-aaf"),
    }
    fn = table.get(cmd)
    if fn is None:
        print(f"unknown command {cmd!r}\n")
        print(__doc__)
        return 2
    fn(rest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
