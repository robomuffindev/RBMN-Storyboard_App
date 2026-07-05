# RBMN CLI Troubleshooting Suite (`tools/rbmn.py`)

One entry point for inspecting and exercising everything the app does, without
clicking through the UI. Built so debugging sessions don't require relaying
terminal output by hand: **every command mirrors its full output to
`diagnostics/latest_<command>.txt`** (plus a timestamped copy) inside the repo,
where Claude can read it directly from the mounted folder.

```
python tools/rbmn.py <command> [args] [--db PATH] [--port N]
```

- `--db PATH` — override the DB location (default `~/RBMN-Projects/RBMN.db`).
- `--port N` — override the backend port (default: read from `app_settings.app_port`, else 8899).
- `<match>` — a project **name fragment or id fragment**; must match exactly one project, otherwise all candidates are listed.
- Inspection commands open the DB **read-only** (`mode=ro`) — always safe, even while the app runs.
- The `diagnostics/` folder is gitignored. Timestamped copies accumulate; delete freely.

---

## Inspection commands (backend NOT required)

### `projects`
Lists every project: id, mode, scene/chapter counts, active flags
(`AAF` audio authority, `2PASS`, `IDEO` ideogram JSON, `INTENT` scene intent,
`VJSON` video JSON), name. **Start here to get the exact id for other commands.**

### `project <match>`
Deep snapshot of one project: resolved project directory (+ whether it exists
on disk), selected settings (audio_source, aaf_import, prompt modes, color
override, resolutions...), character roster, chapter tree with sources, and a
per-scene table with content marks — `F`=first frame, `L`=last frame,
`V`=video, `A`=audio clip, `S`=story flow — plus character-ref selection and
prompt length. One glance shows what a project has and is missing.

### `prompts <match> [scene_idx]`
Per scene: stored FF prompt, `submitted_image_prompt` (what was ACTUALLY sent
after dispatch mutations), last-frame + video prompts (stored and submitted),
flow_idea, LLM instructions, and mode flags (`image_refs_first[_manual]`,
two-pass, intent/JSON modes). The stored-vs-submitted diff is the fastest way
to see whether a dispatch mutation (SFW suffix, color override, JSON swap)
did something unexpected.

### `jobs <match> [N]`
Last N (default 25) jobs: timestamp, status, type, `workflow_type` vs
`_effective_workflow_type` (redirect visibility), and the first 300 chars of
any error.

### `audio <match>` · `timeline <match>` · `chapters <match>` · `general`
Wrappers around the specialized diag scripts (`diag_audio.py`,
`diag_timeline.py`, `diag_chapters.py`, `diag.py`) — same output, now also
mirrored to `diagnostics/`. `audio` includes master/clip existence, codec
checks, and per-clip content fingerprints ("all clips identical" detection).

### `db`
Global health: DB + WAL size, row counts per table, stuck pending/running
jobs, and key `app_settings` fields (`project_dir` override, `app_port`,
model/LLM selections, SFW flags).

### `aaf <file.aaf>`
Inspect any AAF before/after import: clip count and names, per-track
breakdown, mob topology (**check `MasterMob:` count** — ElevenLabs uses ONE
MasterMob with N slots), embedded essence formats/sizes, user comments, and a
verdict on whether audio/text are inside.

### `media <file>`
ffprobe (codec, sample rate, channels, duration, size) + an md5 content
fingerprint of the first 2 s of decoded audio. Compare fingerprints across
scene clips to prove whether they contain different audio. Catches the
classic `mp3-packets-inside-.wav` bug via `codec` ≠ `pcm_s16le`.

### `logs [N] [grep]`
Tails `logs/rbmn.log` (the backend's rotating file log — always on). `logs
300` = last 300 lines; `logs 200 slice` = last 200 lines containing "slice".

---

## Live-backend commands (app must be running)

### `health`
`GET /api/health` — confirms the backend is up, on which port, and its version.

### `api <METHOD> <path> [json]`
Raw escape hatch to ANY endpoint:
```
python tools/rbmn.py api GET  /api/projects
python tools/rbmn.py api POST /api/projects/<id>/generate/auto '{"mode":"missing"}'
```
Errors return the real HTTP status + response body (first 2000 chars).

### `slice <match>`
`POST .../timeline/slice-audio` — regenerate every per-scene audio clip from
the newest master. The repair step for any per-scene audio weirdness.

### `detach-aaf <match>`
`POST .../timeline/detach-aaf` — clear the AAF-authoritative flag (scenes,
audio, chapters untouched).

---

## Recipes (symptom → commands)

| Symptom | Run |
|---|---|
| "Scene audio sounds wrong/identical" | `audio <p>` → fingerprints; then `slice <p>`; then `audio <p>` again |
| "Import/generation did nothing" | `logs 300` → then `jobs <p>` for errors |
| "Prompt came out weird" | `prompts <p> <scene#>` → compare stored vs submitted |
| "Timeline boundaries look wrong" | `timeline <p>` + `chapters <p>` |
| AAF surprises (no audio/text, odd cuts) | `aaf <file>` — read the topology + verdict lines |
| "Is the app even healthy?" | `db` (offline) + `health` (online) |
| "Which project id was that?" | `projects` |
| One suspicious media file | `media <path>` |

## How Claude uses this (the intended workflow)

1. Lorenzo reports a symptom (or nothing — Claude starts on its own).
2. Claude reads `logs/rbmn.log` and any `diagnostics/latest_*.txt` directly
   from the repo — no copy/paste needed.
3. If fresh data is needed, either Claude runs the command itself (when a
   shell on the machine is available) or asks Lorenzo to run **one** short
   command — the output lands in `diagnostics/` automatically either way.
4. For live-process checks, `health` first, then `api`/`slice`/`detach-aaf`
   against the running backend.

## Extending the suite

Add a `cmd_<name>(argv)` function in `tools/rbmn.py`, print through a
`Tee("<name>")`, call `t.flush_to_disk()`, register in the `table` dict in
`main()`, and document it here. Wrapper-style commands (running an existing
`tools/diag_*.py`) only need an entry in the `wrapped` dict. Rules: inspection
commands must open the DB read-only; anything that mutates state must go
through the backend HTTP API (never write the DB directly — the app owns it).
