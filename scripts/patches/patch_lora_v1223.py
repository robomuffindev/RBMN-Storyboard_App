"""v1.223 — the render path had no write lock, and lost most of its bookkeeping.

`_qc_blocking` guards its read-modify-write with a `_th.Lock()`.  `_render_blocking`
does the SAME read-whole-file / mutate / write-whole-file from one thread per
worker, and guards nothing.  So when two renders finish close together the
second one's read predates the first one's write, and the first one's update is
silently thrown away.

The image itself always survives — `_save_png_bytes` happens before the read —
but everything recorded ABOUT it can vanish:

  * `status = "done"`  -> a re-plan then treats the row as never rendered and
                          DELETES the file.  This is how 40 images on disk came
                          out as "7 of 40 rendered".
  * `attempts`         -> the repair loop's MAX_ATTEMPTS cap silently under-counts,
                          so a stuck image can be re-rolled far more than 3 times.
  * `identity`         -> the "which base was used" column, i.e. the data the
                          three-quarter finding was read from.
  * `caption`          -> auto-captions quietly missing on some rows.

Same fix as QC: one module-level lock around the read-modify-write.  Module level
rather than per-call because `dataset_repair` runs render and QC passes in the
same job and both mutate the same file.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── `threading` was only imported inside _qc_blocking ─────────────────────
rep("""import random
import re""",
    """import random
import re
import threading as _th""",
    "module-level threading")

# ── the lock itself ─────────────────────────────────────────────────────────
rep('''_RUNS: Dict[str, dict] = {}       # ds_id -> live job state (worker/status per item)''',
    '''_RUNS: Dict[str, dict] = {}       # ds_id -> live job state (worker/status per item)

# Every mutation of a dataset.json that happens from a worker thread goes through
# this.  v1.223: the render path had none, so concurrent completions clobbered
# each other's status/attempts/identity and a later re-plan deleted images it
# believed were never rendered.  Module-level, not per-call: `dataset_repair`
# interleaves render and QC passes over the SAME file.
_DS_WRITE_LOCK = _th.Lock()''',
    "the lock")

# ── render results ─────────────────────────────────────────────────────────
rep('''    def on_result(jb, data):
        _save_png_bytes(data, _item_path(ds_id, jb["key"]))
        cur = _read_ds(ds_id)
        for it in cur["items"]:
            if it["id"] == jb["key"]:
                it["status"] = "done"
                it["identity"] = jb.get("identity")
                it["seed"] = jb.get("seed")
                it["attempts"] = int(it.get("attempts") or 0) + 1
                if not (it.get("caption") or "").strip():
                    it["caption"] = _caption(cur, it)
        _write_ds(cur)''',
    '''    def on_result(jb, data):
        _save_png_bytes(data, _item_path(ds_id, jb["key"]))
        # v1.223: this whole read-modify-write must be atomic.  Without the lock
        # a second worker finishing mid-sequence read the pre-update file and
        # wrote it back, discarding this image's status, attempts, identity and
        # caption — while the PNG stayed on disk, so nothing looked wrong until
        # a re-plan deleted every row it thought had never rendered.
        with _DS_WRITE_LOCK:
            cur = _read_ds(ds_id)
            for it in cur["items"]:
                if it["id"] == jb["key"]:
                    it["status"] = "done"
                    it["identity"] = jb.get("identity")
                    it["seed"] = jb.get("seed")
                    it["attempts"] = int(it.get("attempts") or 0) + 1
                    if not (it.get("caption") or "").strip():
                        it["caption"] = _caption(cur, it)
            _write_ds(cur)''',
    "render: lock the write")

# ── QC used a per-call lock; share the module one so the two agree ─────────
rep('''    lock = _th.Lock()''',
    '''    lock = _DS_WRITE_LOCK        # shared: repair interleaves render and QC''',
    "qc: share the lock")

# ── a rendered image whose status was lost must not be deleted ────────────
rep('''    KEYS = ("framing", "angle", "expression", "pose", "lighting", "background")
    old = {it["id"]: it for it in ds.get("items", [])}
    kept, lost = [], []
    for it in fresh:
        prev = old.get(it["id"])
        if prev and prev.get("status") == "done":''',
    '''    KEYS = ("framing", "angle", "expression", "pose", "lighting", "background")
    old = {it["id"]: it for it in ds.get("items", [])}
    kept, lost = [], []
    for it in fresh:
        prev = old.get(it["id"])
        # v1.223: trust the FILE, not the status field.  Datasets written before
        # the lock have rows whose status was lost to the race even though the
        # image is right there on disk; going by status alone deletes them.
        if prev and (prev.get("status") == "done"
                     or _item_path(ds.get("id", ""), it["id"]).exists()):''',
    "impact: trust the file")

rep('''    gone = [i for i, p in old.items()
            if p.get("status") == "done" and i not in {f["id"] for f in fresh}]''',
    '''    gone = [i for i, p in old.items()
            if (p.get("status") == "done" or _item_path(ds.get("id", ""), i).exists())
            and i not in {f["id"] for f in fresh}]''',
    "impact: dropped rows too")

rep('''    for it in fresh:
        prev = old.get(it["id"])
        if prev and prev.get("status") == "done" and all(
                prev.get(k) == it.get(k) for k in ("framing", "angle", "expression", "pose",
                                                   "lighting", "background")):''',
    '''    for it in fresh:
        prev = old.get(it["id"])
        # same rule as _plan_impact: an image on disk counts as rendered even if
        # the race ate its status field
        if prev and (prev.get("status") == "done"
                     or _item_path(ds_id, it["id"]).exists()) and all(
                prev.get(k) == it.get(k) for k in ("framing", "angle", "expression", "pose",
                                                   "lighting", "background")):''',
    "replan: preserve by file existence")

# ── and a repair to fix datasets already scrambled ────────────────────────
rep('''@router.post("/datasets/{ds_id}/plan-preview")''',
    '''@router.post("/datasets/{ds_id}/resync")
async def dataset_resync(ds_id: str):
    """Rebuild `status` from what is actually on disk.

    v1.223: datasets written before the render lock have rows whose status was
    lost to the write race while the PNG survived. Nothing is rendered or
    deleted here — it only makes the bookkeeping agree with the filesystem."""
    ds = _read_ds(ds_id)
    fixed, cleared = 0, 0
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        for it in ds.get("items", []):
            on_disk = _item_path(ds_id, it["id"]).exists()
            if on_disk and it.get("status") != "done":
                it["status"] = "done"
                fixed += 1
            elif not on_disk and it.get("status") == "done":
                it["status"] = "planned"
                cleared += 1
        _write_ds(ds)
    logger.info("lora resync[%s]: %d marked rendered, %d cleared", ds_id, fixed, cleared)
    return {"marked_rendered": fixed, "cleared": cleared,
            "rendered": sum(1 for it in ds["items"] if it.get("status") == "done"),
            "total": len(ds["items"]),
            "note": ("These images were on disk but recorded as unrendered — the pre-v1.223 "
                     "write race. A re-plan would have deleted them.") if fixed else
                    "Bookkeeping already matched the filesystem."}


@router.post("/datasets/{ds_id}/plan-preview")''',
    "route: resync")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
