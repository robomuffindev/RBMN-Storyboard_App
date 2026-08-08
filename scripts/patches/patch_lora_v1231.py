"""v1.231 — "no run recorded" could not tell you which of two things it meant.

`_RUNS` is an in-memory dict. It dies with the process. So after a `run.bat`
restart the API reports no run for a dataset that was fully checked minutes
earlier, and both the UI and `watch_run.ps1` say "nothing is going on" — which
is TRUE and useless, because the question being asked is "did my QC actually
happen?" and the answer is not in that dict.

The durable evidence was always in the data: every checked item carries
`qc.checked_at`. Nothing read it.

`_last_activity` derives, from the dataset itself:
  * when QC last ran, and over how many images
  * when a render last landed
  * whether either happened within the last few minutes

so a restart can no longer erase the answer, and "nothing is going on right now"
becomes distinguishable from "nothing has ever happened".
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


rep('''def _public(ds: dict) -> dict:''',
    '''def _last_activity(ds: dict) -> dict:
    """What actually happened to this dataset, read from the DATA.

    v1.231: `_RUNS` is in-memory and does not survive a restart, so it could not
    answer "did my QC run?" — it could only answer "is one running in THIS
    process". Every checked item stamps `qc.checked_at`; that is the durable
    record and nothing was reading it."""
    from datetime import datetime, timezone
    stamps = [str((it.get("qc") or {}).get("checked_at") or "")
              for it in ds.get("items", []) if (it.get("qc") or {}).get("checked_at")]
    out: Dict[str, Any] = {"qc_last": max(stamps) if stamps else None,
                           "qc_count": len(stamps),
                           "rendered": sum(1 for it in ds.get("items", [])
                                           if it.get("status") == "done")}
    if stamps:
        try:
            t = datetime.fromisoformat(max(stamps))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            out["qc_age_s"] = int((datetime.now(timezone.utc) - t).total_seconds())
        except (ValueError, TypeError):
            out["qc_age_s"] = None
    # how many of the most recent batch — a targeted run only re-stamps its own
    # rows, so "12 checked 40 seconds ago" is the shape of a subset QC pass
    if stamps:
        newest = max(stamps)[:16]          # to the minute
        out["qc_last_batch"] = sum(1 for s in stamps if s[:16] == newest)
    return out


def _public(ds: dict) -> dict:''',
    "last activity")

rep('''    return {**ds, "items": items, "run": _RUNS.get(ds["id"]) or None,
            "flags": _flag_summary(ds), "max_attempts": MAX_ATTEMPTS,''',
    '''    return {**ds, "items": items, "run": _RUNS.get(ds["id"]) or None,
            "last_activity": _last_activity(ds),
            "flags": _flag_summary(ds), "max_attempts": MAX_ATTEMPTS,''',
    "expose it")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
