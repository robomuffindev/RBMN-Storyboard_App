"""v1.210.0 — re-render flagged, and an auto-repair loop until the set is clean.

Lorenzo after his first QC pass: 15 of 40 flagged. He wants a "re-render all
flagged" button and a loop that keeps going (render → QC → render …) until
nothing is flagged.

Built with two guards, because an unbounded loop on his hardware is his runs
being spent while he is not looking:
  * a ROUND cap (default 3, max 6) and an early exit the moment nothing is
    flagged, and
  * a per-image ATTEMPT counter — an image that fails three renders is a bad
    plan row, not bad luck, so it is parked as "needs attention" instead of
    being re-rolled forever.
Also adds a FLAG BREAKDOWN (which check failed, how often) so the 15/40 can be
read instead of guessed at: artifacts and bad crops mean the renders need
fixing, while framing/angle/expression misses usually mean the QC is stricter
than the shot list.

Render and QC are lifted into reusable helpers so /generate, /qc and /repair
cannot drift apart.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v1210.py <path-to-lora.py>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


# ── 1. shared helpers: build jobs, render, QC ────────────────────────────
rep(
    '''# ══ routes: dataset CRUD ═════════════════════════════════════════════════════''',
    '''MAX_ATTEMPTS = 3          # after this many renders an image is a plan problem
MAX_ROUNDS = 6            # hard ceiling on the auto-repair loop


def _flag_summary(ds: dict) -> dict:
    """WHY images are flagged, counted.  artifacts / bad crops point at the
    renders; framing / angle / expression misses usually point at the checker."""
    out = {"flagged": 0, "checked": 0, "artifacts": 0, "cropped_badly": 0,
           "framing_off": 0, "angle_off": 0, "expression_off": 0,
           "not_one_person": 0, "face_unclear": 0, "stuck": 0, "top_issues": {}}
    for it in ds.get("items", []):
        q = it.get("qc") or {}
        if not q:
            continue
        out["checked"] += 1
        if q.get("ok") is False:
            out["flagged"] += 1
        if q.get("artifacts"):
            out["artifacts"] += 1
        if q.get("cropped_badly"):
            out["cropped_badly"] += 1
        if q.get("framing_ok") is False:
            out["framing_off"] += 1
        if q.get("angle_ok") is False:
            out["angle_off"] += 1
        if q.get("expression_ok") is False:
            out["expression_off"] += 1
        if q.get("one_person") is False:
            out["not_one_person"] += 1
        if q.get("face_clear") is False:
            out["face_unclear"] += 1
        for phrase in (q.get("issues") or [])[:3]:
            key = str(phrase).strip().lower()[:60]
            if key:
                out["top_issues"][key] = out["top_issues"].get(key, 0) + 1
        if int(it.get("attempts") or 0) >= MAX_ATTEMPTS and q.get("ok") is False:
            out["stuck"] += 1
    out["top_issues"] = dict(sorted(out["top_issues"].items(),
                                    key=lambda kv: -kv[1])[:6])
    return out


def _flagged_ids(ds: dict, include_stuck: bool = False) -> List[str]:
    return [it["id"] for it in ds.get("items", [])
            if (it.get("qc") or {}).get("ok") is False
            and (include_stuck or int(it.get("attempts") or 0) < MAX_ATTEMPTS)]


def _render_jobs(ds: dict, char: dict, items: List[dict], seed0: int) -> List[dict]:
    jobs = []
    for n, it in enumerate(items):
        ang = _by_key(ANGLES, it["angle"])
        base, src_label = _base_for_view(ds["char_slug"], char, ang[3])
        if not base:
            raise HTTPException(409, "this character has no base image yet — strip or tag one "
                                     "in Klein 3.0 first")
        refs = [str(base)]
        face = _refs_by_tag(char, "face")
        if it["framing"] in ("face", "headshot") and face:
            fp = _cdir(ds["char_slug"]) / "refs" / f"{face[-1]['id']}.png"
            if fp.exists():
                refs.append(str(fp))     # close-ups get the face reference too
        jobs.append({"key": it["id"], "prompt": _render_prompt(ds, it), "refs": refs,
                     "w": it.get("width", 896), "h": it.get("height", 1152),
                     "seed": seed0 + n, "identity": src_label})
    return jobs


def _render_blocking(ds_id: str, disp, jobs: List[dict], st: dict) -> None:
    """Fan the render jobs across every klein worker and land the results.
    Blocking — call it from a background thread."""
    def on_result(jb, data):
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
        _write_ds(cur)
        st["done"] = sum(1 for t in st.get("tasks", {}).values()
                         if t.get("status") == "done") + 1
        st["detail"] = f"{st['done']}/{len(jobs)}"

    _parallel_klein_edits(disp, jobs, on_result, st)


def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict) -> None:
    """Vision QC over the given images, one thread per Ollama server.  Blocking."""
    import queue as _q
    import threading as _th
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    qq: Any = _q.Queue()
    for pid in item_ids:
        qq.put(pid)
    lock = _th.Lock()
    st.setdefault("tasks", {})

    def _worker(url: str):
        short = str(url).replace("http://", "").replace("https://", "").rstrip("/")
        while True:
            try:
                iid = qq.get_nowait()
            except Exception:  # noqa: BLE001
                return
            st["tasks"][iid] = {"server": short, "status": "running"}
            try:
                cur = _read_ds(ds_id)
                item = next(x for x in cur["items"] if x["id"] == iid)
                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM, _qc_prompt(item),
                    [_wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())],
                    0.1, 150.0, True)
                data = json.loads(raw) if raw else {}
                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}
                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"])
                with lock:
                    cur = _read_ds(ds_id)
                    for x in cur["items"]:
                        if x["id"] == iid:
                            x["qc"] = {"ok": ok, "checked_at": _now(),
                                       "server": short, **flags, "issues": issues}
                    _write_ds(cur)
                st["tasks"][iid]["status"] = "done"
            except Exception as e:  # noqa: BLE001
                st["tasks"][iid] = {"server": short, "status": "error", "error": str(e)[:160]}
                logger.warning("lora qc[%s/%s] failed: %s", ds_id, iid, e)
            st["done"] = st.get("done", 0) + 1
            st["detail"] = f"{st['done']}/{len(item_ids)}"

    threads = [_th.Thread(target=_worker, args=(u,), daemon=True) for u in urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ══ routes: dataset CRUD ═════════════════════════════════════════════════════''',
    "shared helpers",
)

# ── 2. the payload carries the breakdown ────────────────────────────────
rep(
    '''    return {**ds, "items": items, "run": _RUNS.get(ds["id"]) or None,''',
    '''    return {**ds, "items": items, "run": _RUNS.get(ds["id"]) or None,
            "flags": _flag_summary(ds), "max_attempts": MAX_ATTEMPTS,''',
    "_public flags",
)

# ── 3. generate uses the helpers ────────────────────────────────────────
rep(
    '''    seed0 = random.randint(1, 2_000_000_000)
    jobs = []
    for n, it in enumerate(todo):
        ang = _by_key(ANGLES, it["angle"])
        base, src_label = _base_for_view(ds["char_slug"], char, ang[3])
        if not base:
            raise HTTPException(409, "this character has no base image yet — strip or tag one "
                                     "in Klein 3.0 first")
        refs = [str(base)]
        face = _refs_by_tag(char, "face")
        if it["framing"] in ("face", "headshot") and face:
            fp = _cdir(ds["char_slug"]) / "refs" / f"{face[-1]['id']}.png"
            if fp.exists():
                refs.append(str(fp))     # close-ups get the face reference too
        jobs.append({"key": it["id"], "prompt": _render_prompt(ds, it), "refs": refs,
                     "w": it.get("width", 896), "h": it.get("height", 1152),
                     "seed": seed0 + n, "identity": src_label})
    st = {"status": "running", "kind": "generate", "done": 0, "total": len(jobs),
          "detail": f"0/{len(jobs)}"}
    _RUNS[ds_id] = st

    def on_result(jb, data):
        _save_png_bytes(data, _item_path(ds_id, jb["key"]))
        cur = _read_ds(ds_id)
        for it in cur["items"]:
            if it["id"] == jb["key"]:
                it["status"] = "done"
                it["identity"] = jb.get("identity")
                it["seed"] = jb.get("seed")
                if not (it.get("caption") or "").strip():
                    it["caption"] = _caption(cur, it)
        _write_ds(cur)
        st["done"] = sum(1 for t in st.get("tasks", {}).values() if t.get("status") == "done") + 1
        st["detail"] = f"{st['done']}/{len(jobs)}"

    def _run():
        try:
            _parallel_klein_edits(disp, jobs, on_result, st)''',
    '''    seed0 = random.randint(1, 2_000_000_000)
    jobs = _render_jobs(ds, char, todo, seed0)
    st = {"status": "running", "kind": "generate", "done": 0, "total": len(jobs),
          "detail": f"0/{len(jobs)}"}
    _RUNS[ds_id] = st

    def _run():
        try:
            _render_blocking(ds_id, disp, jobs, st)''',
    "generate uses helpers",
)

# ── 4. qc uses the helper ───────────────────────────────────────────────
rep(
    '''    def _run():
        import queue as _q
        import threading as _th
        from backend.services.character_studio.vnccs_native import wizards as _wiz
        qq: Any = _q.Queue()
        for pid in targets:
            qq.put(pid)
        lock = _th.Lock()

        def _worker(url: str):
            short = str(url).replace("http://", "").replace("https://", "").rstrip("/")
            while True:
                try:
                    iid = qq.get_nowait()
                except Exception:  # noqa: BLE001
                    return
                st["tasks"][iid] = {"server": short, "status": "running"}
                try:
                    cur = _read_ds(ds_id)
                    item = next(x for x in cur["items"] if x["id"] == iid)
                    raw = _wiz.ollama_chat_sync(
                        [url], vision_model, _QC_SYSTEM, _qc_prompt(item),
                        [_wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())],
                        0.1, 150.0, True)
                    data = json.loads(raw) if raw else {}
                    flags = {k: bool(data.get(k)) for k in
                             ("framing_ok", "angle_ok", "expression_ok", "one_person",
                              "face_clear", "artifacts", "cropped_badly")}
                    issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                    ok = (flags["framing_ok"] and flags["one_person"]
                          and not flags["artifacts"] and not flags["cropped_badly"])
                    with lock:
                        cur = _read_ds(ds_id)
                        for x in cur["items"]:
                            if x["id"] == iid:
                                x["qc"] = {"ok": ok, "checked_at": _now(),
                                           "server": short, **flags, "issues": issues}
                        _write_ds(cur)
                    st["tasks"][iid]["status"] = "done"
                except Exception as e:  # noqa: BLE001
                    st["tasks"][iid] = {"server": short, "status": "error", "error": str(e)[:160]}
                    logger.warning("lora qc[%s/%s] failed: %s", ds_id, iid, e)
                st["done"] = st.get("done", 0) + 1
                st["detail"] = f"{st['done']}/{len(targets)}"

        threads = [_th.Thread(target=_worker, args=(u,), daemon=True) for u in urls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        st["status"] = "done"

    _spawn(_run)
    return {"started": True, "total": len(targets), "servers": len(urls)}''',
    '''    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st)
            st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "total": len(targets), "servers": len(urls)}


class RepairIn(BaseModel):
    rounds: int = 3                  # render→QC cycles before stopping
    include_stuck: bool = False      # retry images that already hit MAX_ATTEMPTS
    qc_after: bool = True            # False = one re-render pass, no re-check


@router.post("/datasets/{ds_id}/repair")
async def dataset_repair(ds_id: str, body: RepairIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Re-render every FLAGGED image, re-check it, and repeat until the set is
    clean or the round cap is hit.

    Two brakes, because this spends renders while he is not watching: the round
    cap (<=6) and a per-image attempt counter — an image that fails three
    renders is a bad plan row, not bad luck, so it is parked as stuck and
    reported instead of re-rolled forever.  Each round re-seeds, otherwise a
    re-render reproduces the same picture."""
    ds = _read_ds(ds_id)
    if (_RUNS.get(ds_id) or {}).get("status") == "running":
        raise HTTPException(409, "a run is already going for this dataset")
    char = _load_char(ds["char_slug"])
    todo_ids = _flagged_ids(ds, body.include_stuck)
    if not todo_ids:
        return {"started": False, "note": "nothing is flagged"
                if not _flag_summary(ds)["stuck"]
                else "every remaining flag is on an image that already hit the attempt "
                     "limit — tick 'retry stuck' or fix those rows by hand"}
    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")
    urls: List[str] = []
    vision_model = None
    if body.qc_after:
        from backend.api.vnccs_native import _ollama_cfg
        u, _t, vision_model = await _ollama_cfg(session)
        urls = list(u or [])
        if not urls or not vision_model:
            raise HTTPException(503, "Ollama vision model is not configured — the repair loop "
                                     "needs it to re-check (or send qc_after=false).")
    rounds = max(1, min(int(body.rounds or 1), MAX_ROUNDS))
    st = {"status": "running", "kind": "repair", "round": 1, "rounds": rounds,
          "phase": "render", "done": 0, "total": len(todo_ids),
          "detail": f"round 1/{rounds} · re-rendering {len(todo_ids)}",
          "history": [], "tasks": {}}
    _RUNS[ds_id] = st

    def _run():
        try:
            ids = todo_ids
            for rnd in range(1, rounds + 1):
                cur = _read_ds(ds_id)
                items = [it for it in cur["items"] if it["id"] in set(ids)]
                if not items:
                    break
                st.update({"round": rnd, "phase": "render", "done": 0, "total": len(items),
                           "tasks": {}, "detail": f"round {rnd}/{rounds} · re-rendering {len(items)}"})
                jobs = _render_jobs(cur, char, items, random.randint(1, 2_000_000_000))
                _render_blocking(ds_id, disp, jobs, st)
                if not body.qc_after:
                    st["history"].append({"round": rnd, "rendered": len(items), "flagged": None})
                    break
                st.update({"phase": "qc", "done": 0, "total": len(items), "tasks": {},
                           "detail": f"round {rnd}/{rounds} · re-checking {len(items)}"})
                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st)
                cur = _read_ds(ds_id)
                still = _flagged_ids(cur, body.include_stuck)
                st["history"].append({"round": rnd, "rendered": len(items),
                                      "flagged": len(still)})
                logger.info("lora repair[%s] round %d/%d: %d re-rendered, %d still flagged",
                            ds_id, rnd, rounds, len(items), len(still))
                if not still:
                    st["detail"] = f"clean after {rnd} round(s)"
                    break
                ids = still
                st["detail"] = f"round {rnd}/{rounds} done · {len(still)} still flagged"
            final = _flag_summary(_read_ds(ds_id))
            st["summary"] = final
            st["status"] = "done"
            if final["flagged"]:
                st["detail"] = (f"{final['flagged']} still flagged"
                                + (f" ({final['stuck']} at the attempt limit)"
                                   if final["stuck"] else ""))
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    logger.info("lora repair[%s]: %d flagged, up to %d round(s)", ds_id, len(todo_ids), rounds)
    return {"started": True, "total": len(todo_ids), "rounds": rounds}''',
    "qc helper + repair loop",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
