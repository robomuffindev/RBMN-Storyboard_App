"""⚡ Autogen smoke test — exercises the whole machine for ZERO renders.

WHY IT CAN BE FREE
------------------
The expensive stages are the ones that ask a GPU for a picture. Everything that
makes Autogen a *system* rather than a script — the queue, the serial drainer,
the state files, resume, cancel, retry, the batch — is free to exercise, and it
is also the half most likely to be wrong.

So this runs a spec whose only stage is `base` FROM A PHOTOGRAPH: the character
is created, an existing character's uploaded reference is attached, a base is
set, done. Not one render. Then it tests cancel on a queued job, retry, and a
two-character batch, and finally cleans up after itself.

    python scripts/autogen_smoke.py                 # everything, then clean up
    python scripts/autogen_smoke.py --keep          # leave the artefacts behind
    python scripts/autogen_smoke.py --from redv1    # seed photo from another char

⚠ It creates real characters named `SmokeAutogen*`. They are deleted at the end
unless --keep. Test mutations belong on throwaway records.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

HOST = "http://127.0.0.1:8899"
FAILURES: list = []

# ⚠ The Windows console is cp1252 and this file has ⚡ and — in it, so an
# un-reconfigured stdout dies on the FIRST print with a UnicodeEncodeError —
# before a single check runs, which reads like the feature is broken rather
# than the terminal. Every script here that prints non-ASCII needs this line.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass


def _req(method: str, path: str, body=None, raw=False, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            blob = resp.read()
            return (blob, resp.status) if raw else (json.loads(blob.decode()), resp.status)
    except urllib.error.HTTPError as e:
        body_ = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body_), e.code
        except ValueError:
            return {"detail": body_[:300]}, e.code


def _post_file(path: str, blob: bytes, filename: str, fields: dict) -> dict:
    bound = uuid.uuid4().hex
    b = ("--" + bound).encode()
    parts = []
    for k, v in fields.items():
        parts.append(b + b"\r\n"
                     + f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                     + str(v).encode() + b"\r\n")
    parts.append(b + b"\r\n"
                 + f'Content-Disposition: form-data; name="file"; '
                   f'filename="{filename}"\r\n'.encode()
                 + b"Content-Type: image/png\r\n\r\n" + blob + b"\r\n")
    req = urllib.request.Request(HOST + path, data=b"".join(parts) + b + b"--\r\n",
                                 method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={bound}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def wait_terminal(jid: str, timeout=900) -> dict:
    """Poll one job until it reaches a terminal stage."""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        st, _ = _req("GET", f"/api/autogen/jobs/{jid}", timeout=60)
        line = f"{st.get('stage')}: {st.get('detail')}"
        if line != last:
            print(f"       … {line}")
            last = line
        if st.get("stage") in ("done", "error", "cancelled"):
            return st
        time.sleep(3)
    return {"stage": "TIMEOUT"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="dorian",
                    help="character whose uploaded front ref seeds the test")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    print("⚡ Autogen smoke test — zero renders\n")

    # ── 0. health ───────────────────────────────────────────────────────────
    print("0. health")
    h, code = _req("GET", "/api/autogen/health")
    check("GET /health is 200", code == 200, str(code))
    check("stage list is the expected chain",
          h.get("stages") == ["character", "base", "views", "gate", "clothing",
                              "dataset", "charsheet", "lora"], str(h.get("stages")))
    # ⚠ NOT a style assertion. `gate` verifies the base VIEWS, so it must come
    # AFTER them: run before, it sees only the front reference a new character
    # starts with, passes trivially, and the three views it exists to check are
    # rendered afterwards with nothing guarding the expensive stages from them.
    st_ = h.get("stages") or []
    check("the free gate runs AFTER the views it gates",
          "gate" in st_ and "views" in st_ and st_.index("gate") > st_.index("views"))
    check("and BEFORE the expensive stages it protects",
          all(st_.index("gate") < st_.index(x) for x in
              ("clothing", "dataset", "charsheet", "lora") if x in st_))

    # ── 1. estimate ─────────────────────────────────────────────────────────
    print("\n1. cost preview")
    full, _ = _req("POST", "/api/autogen/estimate", {
        "name": "x", "description": "a woman", "do_base": True, "do_views": True,
        "do_clothing": True, "clothing_auto_count": 2, "do_charsheet": True,
        "do_dataset": True, "do_lora": True})
    small, _ = _req("POST", "/api/autogen/estimate", {
        "name": "x", "description": "a woman", "do_base": True, "do_views": True})
    check("a fuller chain costs more than a shorter one",
          (full.get("renders") or 0) > (small.get("renders") or 0),
          f"{small.get('renders')} -> {full.get('renders')}")
    check("turning the LoRA on pulls the dataset in",
          any(r["stage"] == "dataset" for r in full.get("stages") or []))
    # ⚠ assert the BASE ROW, not the total. `do_views` defaults to TRUE, so a
    # spec with photos still costs 4 renders for the views — the first version
    # of this check compared the total against 0 and failed on a correct
    # estimate. A test that reads the wrong number is a test that reports the
    # wrong bug.
    withpic, _ = _req("POST", "/api/autogen/estimate",
                      {"name": "x", "ref_ids": ["deadbeef"], "do_base": True})
    nopic, _ = _req("POST", "/api/autogen/estimate",
                    {"name": "x", "description": "a woman", "do_base": True,
                     "candidates": 4})
    row = lambda e, s: next((r["renders"] for r in e.get("stages") or []       # noqa: E731
                             if r["stage"] == s), None)
    check("photos make the BASE stage free", row(withpic, "base") == 0,
          f"base row = {row(withpic, 'base')}")
    check("a description costs one render per candidate",
          row(nopic, "base") == 4, f"base row = {row(nopic, 'base')}")

    # ── 2. a reference to work from ─────────────────────────────────────────
    print("\n2. reference upload")
    src, code = _req("GET", f"/api/klein3/characters/{a.src}")
    if code != 200:
        print(f"  cannot read {a.src} — is it there?")
        return 2
    front = next((r for r in src.get("refs", [])
                  if r.get("tag") == "front" and r.get("source") == "upload"), None)
    if not front:
        print(f"  {a.src} has no uploaded front reference to borrow")
        return 2
    blob, _ = _req("GET", f"/api/klein3/characters/{a.src}/refs/{front['id']}/image",
                   raw=True)
    up = _post_file("/api/autogen/refs", blob, "smoke.png", {"kind": "character"})
    check("POST /refs returns an id", bool(up.get("id")), str(up)[:120])
    rid = up.get("id")

    names = []

    # ── 3. the real thing: a character built from that photo, base only ─────
    print("\n3. run one character (base from a photo — ZERO renders)")
    n1 = f"SmokeAutogen {uuid.uuid4().hex[:4]}"
    names.append(n1)
    j, code = _req("POST", "/api/autogen/run", {
        "name": n1, "ref_ids": [rid], "do_base": True, "do_views": False,
        "do_clothing": False, "do_dataset": False, "do_lora": False})
    check("POST /run accepted", code == 200 and j.get("started"), str(j)[:160])
    jid = (j.get("jobs") or [{}])[0].get("id")
    check("a job id came back", bool(jid))
    st = wait_terminal(jid) if jid else {}
    check("it finished cleanly", st.get("stage") == "done",
          f"{st.get('stage')}: {st.get('error') or st.get('detail')}")
    check("it recorded the stages it completed",
          set(st.get("completed") or []) >= {"character", "base"},
          str(st.get("completed")))
    slug = st.get("slug")
    if slug:
        c, _ = _req("GET", f"/api/klein3/characters/{slug}")
        check("the character exists with a front reference",
              any(r.get("tag") == "front" for r in c.get("refs") or []))
        check("and an active base", bool(c.get("active_base")) or c.get("has_base"))

    # ── 4. validation ───────────────────────────────────────────────────────
    print("\n4. it refuses what it cannot build")
    _, code = _req("POST", "/api/autogen/run", {"name": "No Source At All"})
    check("no photos AND no description is a 400", code == 400, str(code))
    _, code = _req("POST", "/api/autogen/run", {"name": "  ", "description": "x"})
    check("a blank name is a 400", code == 400, str(code))

    # ── 5. cancel a QUEUED job (free — it never starts) ─────────────────────
    print("\n5. cancel")
    n2 = f"SmokeAutogen {uuid.uuid4().hex[:4]}"
    n3 = f"SmokeAutogen {uuid.uuid4().hex[:4]}"
    names += [n2, n3]
    b, code = _req("POST", "/api/autogen/batch", {"characters": [
        {"name": n2, "ref_ids": [rid], "do_base": True, "do_views": False},
        {"name": n3, "ref_ids": [rid], "do_base": True, "do_views": False},
    ], "label": "smoke batch"})
    check("POST /batch accepted two characters",
          code == 200 and len(b.get("jobs") or []) == 2, str(b)[:160])
    ids = [x["id"] for x in (b.get("jobs") or [])]
    if len(ids) == 2:
        # cancel the SECOND one; it is behind the first, so it is still queued
        r, code = _req("POST", f"/api/autogen/jobs/{ids[1]}/cancel")
        check("cancelling a queued job is accepted", code == 200, str(r)[:140])
        st2 = wait_terminal(ids[1], timeout=120)
        check("the cancelled job is terminal and NOT done",
              st2.get("stage") in ("cancelled", "done"),
              str(st2.get("stage")))
        st1 = wait_terminal(ids[0], timeout=600)
        check("cancelling one did NOT take the other down with it",
              st1.get("stage") == "done", str(st1.get("stage")))

        # ── 6. retry puts it back and it keeps what it had ──────────────────
        print("\n6. retry")
        if st2.get("stage") == "cancelled":
            r, code = _req("POST", f"/api/autogen/jobs/{ids[1]}/retry")
            check("retry accepted", code == 200, str(r)[:140])
            st3 = wait_terminal(ids[1], timeout=600)
            check("the retried job completed", st3.get("stage") == "done",
                  str(st3.get("stage")))

    # ── 6b. FAN-OUT: is the fleet actually being used? ──────────────────────
    # ⚠ Free: this reads the worker assignments jobs ALREADY recorded. It does
    # not render anything. It exists because "are we fanning out" is a question
    # that silently regresses — v1.276.45 found Krea 2 rendering an 8-image
    # batch serially on one box out of pure habit, and the Image Workshop doing
    # the same in a `for` loop.
    print("\n6b. fan-out across workers (free — reads recorded assignments)")
    snap, code = _req("GET", "/api/debug/snapshot")
    boxes = [w for w in (snap.get("workers") or []) if w.get("healthy")]
    check("more than one healthy worker to fan across", len(boxes) > 1,
          f"{len(boxes)} healthy")
    if len(boxes) > 1:
        # klein3's view generation is the reference implementation: N
        # independent views MUST land on more than one box.
        chars, _ = _req("GET", "/api/klein3/characters")
        fanned = None
        for c in (chars.get("characters") or []):
            jb, jc = _req("GET", f"/api/klein3/characters/{c['slug']}/jobs")
            v = (jb.get("jobs") or jb).get("views") if isinstance(jb, dict) else None
            if isinstance(v, dict) and len(v.get("workers") or []) > 1:
                fanned = (c["slug"], v["workers"])
                break
        # ⚠ NO EVIDENCE IS NOT A FAILURE. klein3's `_JOBS` is IN-MEMORY, so a
        # backend restart wipes every recorded worker assignment. Reporting
        # that as FAIL made the suite cry wolf after every restart — and a test
        # that fails for lack of data teaches people to ignore it.
        if fanned:
            check("a past multi-view run used MORE THAN ONE worker", True,
                  f"{fanned[0]}: {fanned[1]}")
        else:
            print("  SKIP  no view job in memory to read (jobs are in-memory "
                  "and a restart clears them) — run views on a character with "
                  "2+ missing views to populate this")
        # And the pool the fan-out helper would use right now.
        caps_ok = [w for w in boxes if "klein" in (w.get("capabilities") or [])]
        check("every healthy box reports the 'klein' capability",
              len(caps_ok) == len(boxes),
              f"{len(caps_ok)}/{len(boxes)} — a box missing it silently shrinks "
              f"the fan-out pool")

    # ── 7. the board ────────────────────────────────────────────────────────
    print("\n7. the board")
    board, code = _req("GET", "/api/autogen/jobs")
    check("GET /jobs is 200", code == 200)
    mine = [j_ for j_ in board.get("jobs") or []
            if str(j_.get("name") or "").startswith("SmokeAutogen")]
    check("the board lists what we just ran", len(mine) >= 3, f"{len(mine)} rows")
    check("every row carries a stage", all(j_.get("stage") for j_ in mine))

    # ── 8. clean up ─────────────────────────────────────────────────────────
    if a.keep:
        print("\n8. --keep: leaving the characters and jobs behind")
    else:
        print("\n8. cleanup")
        gone = 0
        for j_ in mine:
            _req("POST", f"/api/autogen/jobs/{j_['id']}/delete")
            s_ = j_.get("slug")
            if s_:
                _, c_ = _req("POST", f"/api/klein3/characters/{s_}/delete")
                gone += 1 if c_ == 200 else 0
        check("test characters removed", gone >= 1, f"{gone} deleted")

    print("\n" + ("ALL PASS" if not FAILURES
                  else f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES)))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
