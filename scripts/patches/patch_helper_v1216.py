"""helper v1.216 — a run's artifacts are more than its weights.

`run_artifacts` globbed `*.safetensors` in the output folder's top level, so
everything Fizgig writes that tells you whether the LoRA is any GOOD was
unreachable over HTTP:

    sample/*.png                    the per-epoch previews — the ONLY way to
                                    judge a checkpoint by eye
    loss_log/per_image_loss.jsonl   which image is dragging, epoch by epoch

The first real training run finished with a best checkpoint at epoch 27 and a
final one measurably worse, and there was no way to look at either.

WHAT CHANGES
    * artifacts are collected RECURSIVELY, and the name is the path relative to
      the output folder (`sample/epoch_000027_00.png`), so subfolders work
    * weights, images, logs and configs are all listed, each tagged with a
      `kind` so a caller can ask for previews without wading through 40
      checkpoints of 117MB each
    * the download route serves the right Content-Type, so a PNG opens in a
      browser instead of downloading as a blob
    * `?kind=image` filters the listing

SAFETY
    A name is resolved against the output folder and REJECTED if it lands
    outside it — `..\\..\\Windows\\System32\\...` does not become a download
    endpoint. The listing is capped at 500 entries so a folder full of states
    cannot make a response enormous.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/worker/rbmn_helper.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''VERSION = "1.215.0"''', '''VERSION = "1.216.0"''', "version")

rep('''    p = Path(out)
    if not out or not p.is_dir():
        return []
    files = []
    for f in sorted(p.glob("*.safetensors")):
        try:
            files.append({"name": f.name, "bytes": f.stat().st_size,
                          "modified": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                    time.localtime(f.stat().st_mtime))})
        except OSError:
            continue
    return files''',
    '''    p = Path(out)
    if not out or not p.is_dir():
        return []
    # v1.216: RECURSIVE, and not just weights. The previews under sample/ and the
    # per-image loss log are the only things that say whether a checkpoint is any
    # good, and neither was reachable.
    kinds = {".safetensors": "weights", ".png": "image", ".jpg": "image",
             ".jpeg": "image", ".webp": "image", ".jsonl": "log", ".json": "log",
             ".txt": "log", ".log": "log", ".toml": "config", ".yaml": "config",
             ".csv": "log"}
    files = []
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        kind = kinds.get(f.suffix.lower())
        if not kind:
            continue
        try:
            files.append({
                # Relative POSIX path, so `sample/epoch_000027_00.png` is a name
                # the download route can take verbatim.
                "name": f.relative_to(p).as_posix(),
                "kind": kind,
                "bytes": f.stat().st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(f.stat().st_mtime))})
        except OSError:
            continue
        if len(files) >= 500:
            break
    return files


# Served so a preview opens in a browser rather than downloading as a blob.
ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".json": "application/json",
                  ".jsonl": "application/x-ndjson", ".txt": "text/plain; charset=utf-8",
                  ".log": "text/plain; charset=utf-8",
                  ".toml": "text/plain; charset=utf-8",
                  ".yaml": "text/plain; charset=utf-8",
                  ".csv": "text/csv; charset=utf-8"}


def artifact_path(cfg: dict, st: dict, want: str) -> Path:
    """Resolve an artifact name inside the run's output folder, or raise.

    The name comes off the URL, so it is resolved and checked to be UNDER the
    output folder — a download route must not become a way to read
    ..\\\\..\\\\Windows\\\\System32."""
    out = (st.get("opts") or {}).get("output_dir") or ""
    if not out:
        fiz = cfg["fizgig"].get("root") or ""
        out = str(Path(fiz) / "output_loras" / (st.get("dataset") or "")) if fiz else ""
    base = Path(out).resolve()
    fp = (base / want).resolve()
    if base != fp and base not in fp.parents:
        raise ValueError("that name resolves outside the run's output folder")
    if not fp.is_file():
        raise FileNotFoundError(want)
    return fp''',
    "recursive artifacts")

rep('''            m = re.match(r"^/runs/([^/]+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                return self.json({**st, "artifacts": run_artifacts(cfg, st)})''',
    '''            m = re.match(r"^/runs/([^/]+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                arts = run_artifacts(cfg, st)
                # v1.216: ?kind=image asks for the previews without wading
                # through forty 117MB checkpoints.
                kind = (q.get("kind") or [""])[0]
                if kind:
                    arts = [a for a in arts if a.get("kind") == kind]
                return self.json({**st, "artifacts": arts,
                                  "artifact_kinds": sorted({a["kind"] for a
                                                            in run_artifacts(cfg, st)})})''',
    "kind filter")

rep('''            m = re.match(r"^/runs/([^/]+)/artifacts/(.+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                want = m.group(2)
                for a in run_artifacts(cfg, st):
                    if a["name"] == want:
                        out = (st.get("opts") or {}).get("output_dir") or \\
                            str(Path(cfg["fizgig"]["root"]) / "output_loras" / st["dataset"])
                        return self._send(200, (Path(out) / want).read_bytes(),
                                          "application/octet-stream")
                return self.fail(404, "no such artifact")''',
    '''            m = re.match(r"^/runs/([^/]+)/artifacts/(.+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                want = unquote(m.group(2))
                try:
                    fp = artifact_path(cfg, st, want)
                except ValueError as e:
                    return self.fail(400, str(e))
                except (FileNotFoundError, OSError):
                    return self.fail(404, f"no artifact {want}")
                ctype = ARTIFACT_TYPES.get(fp.suffix.lower(), "application/octet-stream")
                return self._send(200, fp.read_bytes(), ctype)''',
    "serve any artifact")

rep('''from urllib.parse import parse_qs, urlparse''',
    '''from urllib.parse import parse_qs, unquote, urlparse''',
    "unquote import")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
