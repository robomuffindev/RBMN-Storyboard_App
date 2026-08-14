"""⚡⚡ AUTOGEN v2 — a character from nothing, to whatever point you asked for.

WHAT THIS IS
------------
Every stage of the character lane already exists and is measured. What did not
exist is a way to say "make me this character, up to HERE" and walk away — and
a way to say it for TEN characters and come back to a finished batch.

    description or photos
        -> character record + front reference + base
        -> the four base views          (verified, retried)
        -> clothing                     (designed, approved, adopted, worn)
        -> character sheet
        -> LoRA dataset                 (rendered, captioned, QC'd, repaired)
        -> trained + installed LoRA

Every arrow is a toggle. Turning one off does not skip a step in the middle of
a chain — it TRUNCATES the chain, because each stage is the next one's input.

WHY IT IS A NEW MODULE AND NOT AN EXTENSION OF lora_train._autogen_pipeline
--------------------------------------------------------------------------
That one starts at "a character that already has a base" and ends at a LoRA.
Its job is the DATASET recipe and it does that well. This one starts at
nothing, ends anywhere, and has to run a QUEUE across characters. Bolting a
queue and six optional stages onto it would have left one function that does
both jobs and neither clearly. `_autogen_pipeline` is still the dataset+train
half; this module CALLS the same routes it does.

THE FOUR THINGS THAT DID NOT EXIST ANYWHERE IN THIS LANE (all requested)
-----------------------------------------------------------------------
1. **CANCEL.** Nothing in klein3 / forge / costumes / lora / charsheet could be
   stopped once started; the only stop was killing the backend. A cancel flag
   is checked between every stage AND inside every wait loop, and the workers
   this run touched get `interrupt()` so in-flight GPU work actually stops
   (the pattern is vnccs_native's base-set cancel).
2. **A COST PREVIEW.** A full chain is 40+ renders and hours of GPU. You should
   be able to see that number before you agree to it, not after.
3. **A FREE GATE BETWEEN STAGES.** The base set is upstream of everything; a
   bad one poisons the dataset, the LoRA and the wardrobe. insightface on the
   CPU costs nothing, so the run checks its own base set and STOPS rather than
   spending forty renders on a character whose views are wrong.
4. **RESUME.** State files survived a restart but nothing re-attached to them,
   so a batch interrupted at character 7 of 10 was simply lost.

⚠ THE RULES THIS MODULE INHERITS (all measured, all in docs/KLEIN3.md)
---------------------------------------------------------------------
* Prompts are AFFIRMATIVE. cfg=1, no negative prompt: "no hat" puts a hat on.
* NAME ONLY WHAT IS IN VIEW, and never a franchise name.
* `_app()` BLOCKS — it may only be called from the pipeline THREAD, never from
  an `async def` route (that deadlocks the event loop against itself; see
  `lora_train._app` and v1.276.41).
* State is written BEFORE work is scheduled, because a status set after the
  fact is a status that can lie.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.api.lora import _DS_ROOT as _LORA_DS_ROOT   # import-time root: see
from backend.api.lora_train import _app                  # lora_train.py:45-49
from backend.api.lora_train import _state_load, _state_save

# ⚠ `_app` BLOCKS, so it appears only inside the pipeline THREAD below — never
# in an `async def` route. The routes in this module deliberately do no
# self-calling at all, which is why `_app_async` is not imported here: the
# temptation to reach for it from a route is the bug it exists to work around.

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autogen", tags=["autogen"])

# ⚠ anchored on lora.py's IMPORT-TIME root. cfg.project_dir is overridden from
# the DB after import, so deriving a path at request time silently moves it.
_ROOT = _LORA_DS_ROOT.parent.parent / "autogen"
_JOB_DIR = _ROOT / "jobs"
_REF_DIR = _ROOT / "refs"
_QUEUE_FP = _ROOT / "queue.json"
# ⏸ v1.277.2 — the pause flag lives ON DISK deliberately: its whole reason to
# exist is "pause, reboot the app, come back" without losing a large batch.
_PAUSE_FP = _ROOT / "paused.json"

# in-memory liveness + the cancel flags. The state FILE is the real record;
# these two only say "a thread is alive right now".
_ACTIVE: Dict[str, bool] = {}
_CANCEL: Dict[str, bool] = {}
_WORKERS_TOUCHED: Dict[str, set] = {}
_QUEUE_LOCK = threading.Lock()
_DRAINER: Optional[threading.Thread] = None


# ── stage vocabulary ─────────────────────────────────────────────────────────
# Order matters: it is the dependency order, and the cost preview walks it.
# ⚠⚠ `gate` sits AFTER `views`, and that ordering is the whole point of it.
# It ran BEFORE views in the first version and therefore gated nothing: on a
# fresh character only `front` exists, `views/verify` scores the one ref it can
# see, passes trivially, and the three views it was supposed to check are
# rendered afterwards. A gate upstream of the thing it guards is decoration.
STAGES: List[str] = ["character", "base", "views", "gate", "clothing",
                     "dataset", "charsheet", "lora"]

# What each stage costs in GPU renders, as a function of the spec. Used ONLY by
# the estimate — it is deliberately a simple readable model rather than an
# accurate one, because a wrong estimate that can be reasoned about beats a
# right one that cannot. Seconds are rough per-render wall time on this fleet.
_SECS_PER_RENDER = 22


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "character"


def _ensure_dirs() -> None:
    for d in (_JOB_DIR, _REF_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ── the spec ─────────────────────────────────────────────────────────────────
class ClothingSpec(BaseModel):
    """One outfit to design and put on the character.

    THREE WAYS IN, and they are alternatives rather than a sequence:
      * `description` — a sentence. The TEXT model drafts the 13 garment slots.
      * `slots`       — you already know the slots; typed text wins outright.
      * `ref_ids`     — photographs of the clothing. With references and no
                        typed prompt the reference is VISION-SCANNED and the
                        garment text is built from it, because a typed
                        description and a photograph can disagree and at cfg=1
                        the words win (v1.276.34). Not required.
    """
    name: str = ""
    description: str = ""
    slots: Dict[str, str] = {}
    ref_ids: List[str] = []          # ids from POST /api/autogen/refs
    wearer: str = ""                 # woman | man | unisex ('' = from the character)


class AutogenSpec(BaseModel):
    name: str
    # ── where the character comes from ──────────────────────────────────────
    description: str = ""            # used when there are no reference photos
    fields: Dict[str, str] = {}      # explicit klein3 description fields
    ref_ids: List[str] = []          # uploaded photos (POST /api/autogen/refs)
    ref_tag: str = "front"           # what the FIRST uploaded photo is

    # ── how far to go ───────────────────────────────────────────────────────
    do_base: bool = True
    do_views: bool = True
    do_clothing: bool = False
    do_charsheet: bool = False
    do_dataset: bool = False
    do_lora: bool = False

    # ── clothing ────────────────────────────────────────────────────────────
    clothing: List[ClothingSpec] = []
    clothing_auto_count: int = 0     # "just invent N outfits for them"
    clothing_model: str = "krea2"
    clothing_views: List[str] = ["front", "back", "left", "right", "face"]

    # ── knobs ───────────────────────────────────────────────────────────────
    engine: str = "klein"            # base-character generation engine
    candidates: int = 4              # how many to render before picking
    dataset_total: int = 40
    preset: str = "face_heavy"
    class_token: str = "person"
    trigger: str = ""
    charsheet_preset: str = "standard"
    seed: Optional[int] = None
    stop_on_bad_base: bool = True    # the free gate

    def stages(self) -> List[str]:
        """The stages this spec will actually run, in order.

        `character` and `gate` are not toggles — one is free and one is the
        thing that stops a bad base poisoning everything downstream.
        """
        on = {"character": True, "base": self.do_base, "gate": self.do_views,
              "views": self.do_views, "clothing": self.do_clothing,
              "dataset": self.do_dataset or self.do_lora,
              "charsheet": self.do_charsheet, "lora": self.do_lora}
        return [s for s in STAGES if on.get(s)]


def _outfit_count(spec: AutogenSpec) -> int:
    auto = int(spec.clothing_auto_count or 0)
    return len(spec.clothing) + (max(2, min(auto, 10)) if auto else 0)


def estimate(spec: AutogenSpec) -> Dict[str, Any]:
    """Renders and rough wall time, per stage, BEFORE anything is spent.

    ⚠ These are ESTIMATES and the run does not enforce them. Retries are the
    biggest source of error: a view that fails its facing check costs another
    render, and the dataset repair loop is bounded but variable. The numbers
    below assume a clean run and say so.
    """
    st = spec.stages()
    rows: List[Dict[str, Any]] = []

    def add(stage: str, renders: int, note: str) -> None:
        if stage in st:
            rows.append({"stage": stage, "renders": renders, "note": note})

    have_photo = bool(spec.ref_ids)
    add("character", 0, "creates the record — free")
    add("base", 0 if have_photo else int(spec.candidates),
        "uses your photo — free" if have_photo
        else f"{spec.candidates} candidates, best picked for free on the CPU")
    add("gate", 0, "insightface on the CPU — free")
    add("views", 4, "front/back/left/right (+1 per failed facing check)")
    # ⚠ `lora`'s wardrobe route floors its count at 2, so asking it to invent
    # ONE outfit produces two proposals. Only one is used, but say what will
    # actually be asked for so the estimate is not quietly wrong.
    n_out = _outfit_count(spec)
    add("clothing", n_out * (2 + len(spec.clothing_views)),
        f"{n_out} outfit(s): 2 design candidates + "
        f"{len(spec.clothing_views)} views each" if n_out else "no outfits requested")
    add("dataset", int(spec.dataset_total),
        f"{spec.dataset_total} images + QC/repair rerolls (variable)")
    add("charsheet", 0, "composited from images you already have — free")
    add("lora", 0, "training is GPU time on the trainer box, not renders")

    renders = sum(r["renders"] for r in rows)
    secs = renders * _SECS_PER_RENDER
    if "lora" in st:
        secs += 3600                      # a training run, very roughly
    return {"stages": rows, "renders": renders,
            "seconds": secs, "human": _human(secs),
            "caveat": "assumes a clean run: retries, QC repair rounds and "
                      "model load times are not included, and training time "
                      "is a placeholder rather than a measurement."}


def _human(secs: int) -> str:
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"~{round(secs / 60)} min"
    return f"~{secs / 3600:.1f} h"


# ── state ────────────────────────────────────────────────────────────────────
def _fp(jid: str) -> Path:
    return _JOB_DIR / f"{jid}.json"


def _stage(jid: str, st: dict, stage: str, detail: str = "") -> None:
    """Record a transition, with TIMING. Atomic write — a half-written status
    file read by the poller is a status that lies in a different way.

    ⭐ Timing lives on the SERVER, not in the browser. A client-side stopwatch
    is wrong the moment you reload the page, close the tab, or the backend
    restarts mid-run — and this pipeline runs for hours precisely when you are
    not watching it. Every stage records when it started and how long the
    previous one took, so "how long has this been going" is answerable from the
    state file alone.
    """
    now = time.time()
    prev = st.get("stage")
    # close out the stage that is ending
    if prev and prev != stage and st.get("_stage_t0"):
        st.setdefault("stage_times", {})[prev] = round(now - float(st["_stage_t0"]), 1)
    if prev != stage:
        st["_stage_t0"] = now
        st["stage_started_at"] = _now()
    st.setdefault("t0", now)
    st["stage"] = stage
    st["detail"] = detail
    st["updated_at"] = _now()
    st["elapsed_s"] = round(now - float(st["t0"]), 1)
    st.setdefault("log", []).append(
        {"at": _now(), "t": st["elapsed_s"], "stage": stage, "detail": detail})
    st["log"] = st["log"][-400:]
    _state_save(_fp(jid), st)
    logger.info("autogen %s [%s]: %s %s", jid, _human(int(st["elapsed_s"])),
                stage, detail)


def _tick(jid: str, st: dict, detail: str) -> None:
    """Update the fine-grained detail line, and LOG it when it actually changes.

    ⚠ Progress lines like "rendering 12/40" used to overwrite `detail` silently
    and vanish, so verbose mode would have had nothing but stage transitions to
    show for a four-hour dataset render. Logging every tick would spam (a poll
    every 20s for four hours), so it is logged only when the TEXT changes —
    which is exactly when something happened.
    """
    if detail == st.get("detail"):
        _state_save(_fp(jid), st)
        return
    st["detail"] = detail
    st["updated_at"] = _now()
    st["elapsed_s"] = round(time.time() - float(st.get("t0") or time.time()), 1)
    st.setdefault("log", []).append(
        {"at": _now(), "t": st["elapsed_s"], "stage": st.get("stage"),
         "detail": detail, "tick": True})
    st["log"] = st["log"][-400:]
    _state_save(_fp(jid), st)


class Cancelled(Exception):
    """Raised the moment a cancel is seen, so every wait unwinds the same way."""


class Fatal(Exception):
    """A probe's verdict that waiting longer cannot help.

    ⚠ `_wait` swallows probe exceptions on purpose — a transient failure to
    reach a status route must not fail a four-hour render. But that made every
    "the job reported status:error" check inside a probe DEAD CODE: the raise
    was caught, the probe returned None, and the run span the full timeout
    before reporting a bogus timeout instead of the real error. `Fatal` is the
    channel that says the difference.
    """


def _k3_job(slug: str, kind: str) -> dict:
    """One klein3 job dict, whichever shape the route returns.

    ⚠⚠ `GET /api/klein3/characters/{slug}/jobs` returns the job map DIRECTLY —
    `{"views": {...}}`, NOT `{"jobs": {...}}` — while `GET /characters/{slug}`
    nests the same map under `jobs`. Reading the wrong one yields `{}`, whose
    status is None, which every wait loop here treats as "still starting" — so
    the probe never terminates and the stage times out after 90 minutes having
    done nothing wrong. Both shapes are accepted rather than picking one.
    """
    r = _app("GET", f"/api/klein3/characters/{slug}/jobs", timeout=30)
    if not isinstance(r, dict):
        return {}
    inner = r.get("jobs")
    if isinstance(inner, dict):
        r = inner
    j = r.get(kind)
    return j if isinstance(j, dict) else {}


def _check_cancel(jid: str) -> None:
    if _CANCEL.get(jid):
        raise Cancelled()


def _interrupt_workers(jid: str) -> None:
    """Stop the GPU work this run put on the boxes.

    A cancel that only sets a flag leaves the render you were waiting for
    running to completion — the queue empties eventually but the box stays busy
    for minutes afterwards. vnccs_native's base-set cancel interrupts every
    worker the run touched, and that is the behaviour worth copying.

    Best effort by design: a worker that will not answer is not a reason to
    fail the cancel, because the flag has already done the important half.
    """
    from backend.services.comfyui.client import ComfyUIClient
    for h in list(_WORKERS_TOUCHED.get(jid) or set()):
        try:
            ComfyUIClient(h, timeout=15, skip_health_check=True).interrupt()
            logger.info("autogen %s: interrupted %s", jid, h)
        except Exception:                                        # noqa: BLE001
            logger.info("autogen %s: could not interrupt %s", jid, h)


def _note_workers(jid: str, obj: Any, st: Optional[dict] = None) -> None:
    """Remember which boxes a job status says it used, so cancel can reach them.

    Every job dict in this codebase publishes its workers one of two ways —
    a `workers` list or a `tasks`/`items` map with a `worker` on each entry.
    Both are read here rather than picking one, because the lanes genuinely
    differ and a cancel that only understands half of them is a cancel that
    half works.

    v1.277.1 — when `st` is passed, the boxes are ALSO persisted onto the job
    state as `workers_used`. `_WORKERS_TOUCHED` is in-memory and dies with the
    process, so "where did this render" was unanswerable for a finished run —
    exactly the benchmarking record the board is supposed to keep.
    """
    if not isinstance(obj, dict):
        return
    got: set = set()
    for w in (obj.get("workers") or []):
        if w:
            got.add(str(w))
    # ⚠ v1.276.45 — the SINGULAR `worker` key too. The upscale lanes publish
    # `st["worker"]` (one box, one image) rather than a `workers` list, so a
    # cancel could not reach a running GAN upscale at all. Also `aux_renders`,
    # which is how a render that is not a "task" announces itself.
    if obj.get("worker"):
        got.add(str(obj["worker"]))
    for a in (obj.get("aux_renders") or []):
        if isinstance(a, dict) and a.get("worker"):
            got.add(str(a["worker"]))
    for coll in (obj.get("tasks") or {}, ):
        if isinstance(coll, dict):
            for t in coll.values():
                w = (t or {}).get("worker") or (t or {}).get("server")
                if w:
                    got.add(str(w))
    for it in (obj.get("items") or []):
        w = (it or {}).get("worker")
        if w:
            got.add(str(w))
    if got:
        # ⚠ Some lanes publish a bare IP with NO PORT (costumes records the
        # Krea 2 host as `192.168.12.201`). Left alone that becomes
        # `http://192.168.12.201`, i.e. port 80, and the interrupt goes
        # nowhere — a stop button that silently does not stop that lane.
        norm = set()
        for h in got:
            h = str(h)
            if not h.startswith("http"):
                h = f"http://{h}"
            tail = h.split("//", 1)[1]
            if ":" not in tail:
                h = f"{h}:8188"
            norm.add(h)
        _WORKERS_TOUCHED.setdefault(jid, set()).update(norm)
        # durable record for the board + future benchmarking. Saved only when
        # something NEW appeared — this runs on every status poll.
        if st is not None:
            cur = set(st.get("workers_used") or [])
            if not norm <= cur:
                st["workers_used"] = sorted(cur | norm)
                _state_save(_fp(jid), st)


def _wait(jid: str, probe: Callable[[], Optional[Any]], timeout_s: int,
          every: int, what: str) -> Any:
    """Poll `probe` until it returns something truthy, honouring cancel.

    ⚠ The cancel check lives INSIDE the loop, not around it. A four-hour
    dataset render with the check outside would ignore a cancel for four hours,
    which is indistinguishable from a cancel button that does not work.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        _check_cancel(jid)
        try:
            got = probe()
        except (Cancelled, Fatal):
            # ⚠ Fatal must pass THROUGH. A probe that has seen the underlying
            # job report status:"error" is not having a bad moment — waiting
            # longer cannot help, and swallowing it turns a clear error into a
            # 60-minute timeout with the wrong message on it.
            raise
        except Exception as e:                                   # noqa: BLE001
            # A transient failure to reach a status route, on the other hand,
            # must NOT kill a four-hour render.
            logger.info("autogen wait(%s): probe failed: %s", what, e)
            got = None
        if got:
            return got
        time.sleep(every)
    raise RuntimeError(f"timed out after {timeout_s}s waiting for {what}")


# ── the free candidate scorer ────────────────────────────────────────────────
def _usability(path: Path) -> Dict[str, Any]:
    """How usable is this image as a character's FRONT reference? Free, CPU.

    ⚠ This is NOT a likeness score, and it must not be described as one. A
    character invented from a description has no ground truth to be like — so
    what is measurable is whether the image can DO the job the base reference
    has to do: exactly one person, a face the detector is confident about,
    facing the camera, and big enough in frame to crop an anchor out of.
    """
    from backend.services import likeness as lk
    pv = lk.pose(path)
    if not pv:
        return {"ok": False, "score": 0.0, "why": "no face detected"}
    faces = int(pv.get("faces") or 0)
    det = float(pv.get("det_score") or 0.0)
    kps = pv.get("kps_yaw")
    fh = float(pv.get("face_h_ratio") or 0.0)
    why = []
    score = det                                   # 0..1, the detector's own confidence
    if faces != 1:
        score -= 0.35
        why.append(f"{faces} faces")
    if kps is not None and abs(float(kps)) > 1.0:
        score -= 0.20
        why.append(f"not frontal (kps {float(kps):+.2f})")
    if fh < 0.045:
        score -= 0.15
        why.append(f"face is small in frame ({fh:.3f})")
    return {"ok": score > 0.35, "score": round(float(score), 4),
            "faces": faces, "det": round(det, 3),
            "kps_yaw": kps, "face_h_ratio": fh,
            "why": ", ".join(why) or "one clear frontal face"}


# ── the stages ───────────────────────────────────────────────────────────────
# Each takes (jid, st, spec, slug) and returns nothing. They talk to the rest
# of the app over its own HTTP API through _app(), which is safe HERE because
# every one of these runs on the pipeline thread, never on the event loop.

def _s_character(jid: str, st: dict, spec: AutogenSpec) -> str:
    """Create (or resume) the character record and write its description fields.

    forge's create is used rather than klein3's because it RESUMES on an
    existing slug where klein3's 409s. Re-running Autogen on a character you
    already started should carry on, not refuse.
    """
    _stage(jid, st, "character", f"creating {spec.name!r}")
    c = _app("POST", "/api/forge/characters", {"name": spec.name}, timeout=60)
    slug = c.get("slug") or _slugify(spec.name)
    st["slug"] = slug
    st["resumed"] = bool(c.get("resumed"))

    # ⚠ `POST /fields` REPLACES the whole dict (klein3 assigns `c["fields"] =
    # clean`). Sending only `additional_details` on a re-run therefore WIPED
    # age/sex/race/hair/eyes/face/body/height — the fields every view prompt is
    # built from. Merge onto what is already there.
    fields = dict((c.get("fields") if isinstance(c, dict) else None) or {})
    try:
        fields.update({k: v for k, v in
                       (_app("GET", f"/api/klein3/characters/{slug}", timeout=60)
                        .get("fields") or {}).items() if v})
    except Exception:                                            # noqa: BLE001
        pass
    fields.update({k: v for k, v in (spec.fields or {}).items() if v})
    if spec.description and not fields.get("additional_details"):
        # The description is the character. Putting it in `additional_details`
        # is what makes it reach the VIEW prompts — `_character_garments()`
        # reads that field, and v1.276.14 found the sides inventing their own
        # clothes precisely because it was being thrown away.
        fields["additional_details"] = spec.description.strip()
    if fields:
        _app("POST", f"/api/klein3/characters/{slug}/fields", {"fields": fields},
             timeout=60)
    _stage(jid, st, "character", f"{slug} ready"
           + (" (resumed)" if st["resumed"] else ""))
    return slug


def _s_base(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """Get a front reference and an active base onto the character.

    TWO ROUTES IN, and which one runs is decided by whether you gave it photos:
      * PHOTOS  — upload them as references. Free, and a real photograph is the
                  best reference this lane can have.
      * WORDS   — render `candidates` images from the description, score them
                  for free on the CPU, promote the best to front ref + base.
    """
    c = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
    has_front = any(r.get("tag") == "front" for r in (c.get("refs") or []))

    if spec.ref_ids:
        _stage(jid, st, "base", f"attaching {len(spec.ref_ids)} uploaded reference(s)")
        first = True
        for rid in spec.ref_ids:
            _check_cancel(jid)
            fp = _REF_DIR / f"{rid}.png"
            if not fp.exists():
                logger.warning("autogen %s: reference %s is gone", jid, rid)
                continue
            tag = spec.ref_tag if first else "other"
            got = _upload_ref(slug, fp, tag)
            st.setdefault("refs", []).append({"id": got.get("id"), "tag": tag})
            if got.get("upscaling"):
                # ⚠ A small upload is upscaled IN THE BACKGROUND and the route
                # returns before it lands. Copying it into the base right away
                # captures the pre-upscale file — the v1.276.29 pattern of
                # calling a step done while its output is still unusable.
                _stage(jid, st, "base", f"waiting for the reference upscale "
                                        f"({got.get('upscaling')})")
                try:
                    _wait(jid, lambda: (_k3_job(slug, "refup").get("status")
                                        not in ("running", None)) or None,
                          900, 5, "reference upscale")
                except Cancelled:
                    raise
                except Exception as e:                           # noqa: BLE001
                    logger.info("autogen %s: upscale wait gave up (%s)", jid, e)
            if first and got.get("id"):
                _app("POST", f"/api/klein3/characters/{slug}/base/from_ref",
                     {"ref_id": got["id"]}, timeout=120)
            first = False
        _stage(jid, st, "base", "references attached, base set from the first")
        return

    if has_front and not spec.description:
        _stage(jid, st, "base", "character already has a front reference — keeping it")
        return

    # ── from words ──────────────────────────────────────────────────────────
    prompt = (spec.description or "").strip()
    if not prompt:
        raise RuntimeError("no reference images and no description — "
                           "there is nothing to make this character FROM")
    n = max(1, min(int(spec.candidates or 4), 8))
    _stage(jid, st, "base", f"rendering {n} candidates from the description")
    body = {"engine": spec.engine, "prompt": prompt, "count": n,
            "pose": "fullbody_front", "use_fields": True}
    if spec.seed is not None:
        body["seed"] = int(spec.seed)
    _app("POST", f"/api/forge/characters/{slug}/generate", body, timeout=180)

    def _done():
        r = _app("GET", f"/api/forge/characters/{slug}/status", timeout=30)
        _note_workers(jid, r, st)
        _tick(jid, st, f"candidates {r.get('done', 0)}/{r.get('total', n)}"
                       + (f" on {', '.join(sorted(r.get('workers') or []))}"
                          if r.get("workers") else ""))
        if r.get("status") == "error":
            raise Fatal(f"candidate render failed: {r.get('error')}")
        return r if r.get("status") in ("done", "done_with_errors") else None

    _wait(jid, _done, 3600, 10, "character candidates")

    gal = _app("GET", f"/api/forge/characters/{slug}/gallery", timeout=60)
    # ⚠ the gallery is sorted NEWEST FIRST (forge.py sorts reverse=True), so
    # the images this run just made are at the HEAD. Taking the tail scored the
    # OLDEST n — which on a resumed character means promoting an image from a
    # previous session as the new front reference.
    imgs = list(gal.get("images") or [])[:n]
    if not imgs:
        raise RuntimeError("the candidate render produced no images")

    # ⚠ Scored for USABILITY, not likeness. A character invented from a
    # description has nothing to be like; what it must be is a picture this
    # lane can work from. Free — CPU insightface, no GPU, no worker.
    _stage(jid, st, "base", f"scoring {len(imgs)} candidates (free, CPU)")
    scored = []
    for im in imgs:
        _check_cancel(jid)
        p = _forge_image_path(slug, im.get("id") or "")
        u = _usability(p) if p and p.exists() else {"ok": False, "score": 0.0,
                                                    "why": "image missing"}
        scored.append({"image_id": im.get("id"), **u})
    scored.sort(key=lambda r: r.get("score") or 0.0, reverse=True)
    st["candidates"] = scored
    best = scored[0]
    if not best.get("ok"):
        raise RuntimeError(
            "no usable candidate — best was "
            f"{best.get('score')} ({best.get('why')}). The description may be "
            "producing crowds, back views or faces too small to anchor on.")
    _app("POST", f"/api/forge/characters/{slug}/promote",
         {"image_id": best["image_id"], "also_base": True}, timeout=180)
    _stage(jid, st, "base",
           f"promoted candidate {best['image_id'][:8]} "
           f"(score {best['score']}, {best['why']})")


def _forge_image_path(slug: str, image_id: str) -> Optional[Path]:
    """Where a forge candidate lives on disk, so it can be scored without a
    download. Derived from klein3's character dir, which is the one place that
    knows where the library actually is."""
    if not image_id:
        return None
    try:
        from backend.api.klein3 import _cdir
        return _cdir(slug) / "forge" / f"{image_id}.png"
    except Exception:                                            # noqa: BLE001
        return None


def _upload_ref(slug: str, fp: Path, tag: str) -> dict:
    """multipart upload of one reference, by hand.

    `_app` speaks JSON only, and the reference route is multipart — so rather
    than teach `_app` a second content type this builds the body inline. It is
    the same handful of lines as `scripts/k3_new_char_from_ref.py`.
    """
    import urllib.request
    bound = uuid.uuid4().hex
    b = ("--" + bound).encode()
    parts = []
    for k, v in (("tag", tag), ("upscale", "true")):
        parts.append(b + b"\r\n"
                     + f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                     + str(v).encode() + b"\r\n")
    parts.append(b + b"\r\n"
                 + f'Content-Disposition: form-data; name="file"; '
                   f'filename="{fp.name}"\r\n'.encode()
                 + b"Content-Type: image/png\r\n\r\n" + fp.read_bytes() + b"\r\n")
    payload = b"".join(parts) + b + b"--\r\n"
    req = urllib.request.Request(
        f"http://127.0.0.1:8899/api/klein3/characters/{slug}/refs",
        data=payload, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _s_views(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """The four base views, verified and retried.

    ⚠ THIS IS WHERE THE OLD PIPELINE WAS BROKEN. `lora_train._autogen_pipeline`
    posted `{}` to views/generate — but `ViewsIn.views` is REQUIRED and the
    handler 400s on an empty list, so the missing-views step could never once
    have succeeded. Named explicitly here.
    """
    c = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
    missing = list(c.get("missing_views") or [])
    if not missing:
        _stage(jid, st, "views", "all four views already present — nothing to render")
        return
    _stage(jid, st, "views", f"generating {missing} (verify + retry on)")
    _app("POST", f"/api/klein3/characters/{slug}/views/generate",
         {"views": missing, "verify": True, "max_tries": 3}, timeout=180)

    def _done():
        v = _k3_job(slug, "views")
        _note_workers(jid, v, st)
        if v.get("status") == "error":
            raise Fatal(f"view generation failed: {v.get('error')}")
        if v.get("detail"):
            _tick(jid, st, f"views {v['detail']}"
                           + (f" · {len(v.get('workers') or [])} worker(s)"
                              if v.get("workers") else ""))
        return v if v.get("status") not in ("running", None) else None

    v = _wait(jid, _done, 5400, 15, "base views")
    st["views_job"] = {k: v.get(k) for k in
                       ("status", "detail", "failed", "refs_used", "deferred")}
    c = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
    still = list(c.get("missing_views") or [])
    if still:
        # A MISSING view is a problem the chain can see and stop on; a
        # wrong-facing one filed under its tag is a problem it would silently
        # build a dataset, a LoRA and a wardrobe on top of (v1.276.18).
        raise RuntimeError(f"views still missing after retries: {still}")
    _stage(jid, st, "views", "four views present and verified")


def _s_gate(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """The free check that stops a bad base poisoning everything downstream.

    Costs nothing — insightface on the CPU, no GPU and no worker — and it is
    run BEFORE the expensive half precisely because the dataset, the LoRA and
    the wardrobe all inherit whatever the base set got wrong.
    """
    r = _app("POST", f"/api/klein3/characters/{slug}/views/verify",
             {"demote": False}, timeout=300)
    # ⚠ `failed` is a COUNT, not a list — the failing entries are the not-ok
    # rows. Reading it as a list is a TypeError, and this gate exists to stop
    # the run, so a gate that crashes is worse than no gate.
    rows = r.get("rows") or []
    bad = [x for x in rows if not x.get("ok")]
    st["gate"] = {"checked": r.get("checked"), "failed": r.get("failed"),
                  "bad": [{"view": x.get("view"), "why": x.get("why")} for x in bad]}
    if bad and spec.stop_on_bad_base:
        raise RuntimeError(
            "base set failed its own facing check: "
            + "; ".join(f"{x.get('view')} — {x.get('why')}" for x in bad)
            + ". Stopped BEFORE spending the expensive stages on it: the "
              "dataset, the wardrobe and the LoRA all inherit whatever the "
              "base got wrong. Fix or regenerate those views and re-run, or "
              "set stop_on_bad_base:false to continue anyway.")
    _stage(jid, st, "gate",
           f"checked {r.get('checked', '?')}, failed {len(bad)}"
           + (" — continuing anyway" if bad else " — base set is sound"))


def _s_clothing(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """Design costumes, approve the ones WE made, adopt them, and wear them.

    ⚠ ON AUTO-APPROVAL. Costume designs land as unapproved candidates on
    purpose (v1.276.30) and `adopt` 409s on a candidate — the gate is what
    keeps a shared library from filling with experiments. So this approves
    ONLY the ids this run just created, by id. A costume you designed by hand
    in the studio is untouched and still needs your approval. The gate stays
    meaningful; the chain still runs.

    ⚠ ON THE COSTUME LOCK. `costumes._JOBS` has a single GLOBAL "design" slot —
    one design run app-wide. So outfits are designed one at a time and the
    batch drainer is serial, which is also why it is serial.
    """
    specs = list(spec.clothing or [])
    auto_n = max(0, int(spec.clothing_auto_count or 0))
    if auto_n:
        # "just invent N outfits for them" — the wardrobe route already asks
        # the vision model for outfit ideas that suit THIS character, and it
        # is explicitly a proposal that is never applied on its own.
        try:
            w = _app("POST", f"/api/lora/characters/{slug}/wardrobe",
                     {"count": auto_n}, timeout=600)
            for o in (w.get("outfits") or [])[:auto_n]:
                specs.append(ClothingSpec(name=o.get("name") or "",
                                          description=o.get("desc") or ""))
            _stage(jid, st, "clothing",
                   f"vision model proposed {len(w.get('outfits') or [])} outfit(s)")
        except Exception as e:                                   # noqa: BLE001
            _stage(jid, st, "clothing",
                   f"could not auto-propose outfits ({e}) — continuing with "
                   f"{len(specs)} explicit one(s)")
    if not specs:
        _stage(jid, st, "clothing", "no outfits requested")
        return

    char = _app("GET", f"/api/klein3/characters/{slug}", timeout=60)
    sex = str((char.get("fields") or {}).get("sex") or "").lower()
    default_wearer = "woman" if sex.startswith("f") else "man" if sex.startswith("m") else "unisex"

    made: List[dict] = []
    for i, cs in enumerate(specs, 1):
        _check_cancel(jid)
        label = cs.name or cs.description[:40] or f"outfit {i}"
        _stage(jid, st, "clothing", f"[{i}/{len(specs)}] designing {label!r}")

        slots = dict(cs.slots or {})
        if not slots and cs.description and not cs.ref_ids:
            # A sentence becomes the 13 slots via the TEXT model. Skipped when
            # references are attached: with a photograph the garment should be
            # read OFF the image (v1.276.34), and design does that itself.
            try:
                d = _app("POST", "/api/costumes/draft",
                         {"description": cs.description}, timeout=300)
                slots = d.get("slots") or {}
            except Exception as e:                               # noqa: BLE001
                logger.info("autogen %s: costume draft failed (%s)", jid, e)

        ref_ids = _stash_costume_refs(cs.ref_ids)
        body = {"name": cs.name or label, "slots": slots,
                "wearer": cs.wearer or default_wearer,
                "model": spec.clothing_model, "count": 2,
                "refs": ref_ids, "scan_refs": True}
        if not slots and cs.description and ref_ids:
            body["extra"] = cs.description
        elif not slots and cs.description:
            body["prompt"] = cs.description
        _app("POST", "/api/costumes/design", body, timeout=180)

        def _done():
            j = _app("GET", "/api/costumes/job", timeout=30)
            _note_workers(jid, j, st)
            if j.get("status") == "error":
                raise Fatal(f"costume design failed: {j.get('error')}")
            return j if j.get("status") in ("done", "done_with_errors") else None

        j = _wait(jid, _done, 3600, 10, f"costume design {label!r}")
        ids = [m.get("id") for m in (j.get("made") or []) if m.get("id")]
        if not ids:
            _stage(jid, st, "clothing", f"[{i}/{len(specs)}] {label!r} produced "
                                        f"no image — skipping this outfit")
            continue

        # approve ONLY what this run made, by id
        cid = ids[0]
        _app("POST", f"/api/costumes/{cid}/approve",
             {"name": cs.name or label, "approved": True}, timeout=60)
        adopted = _app("POST", f"/api/costumes/{cid}/adopt",
                       {"slug": slug, "rescan": True}, timeout=600)
        made.append({"costume_id": cid, "name": cs.name or label,
                     "garment_ref": (adopted.get("ref") or {}).get("id")
                     if isinstance(adopted.get("ref"), dict) else adopted.get("ref"),
                     "slots": adopted.get("slots") or slots})
        st["costumes"] = made
        _state_save(_fp(jid), st)

        # ── wear it: render the outfit across the views ─────────────────────
        _check_cancel(jid)
        _stage(jid, st, "clothing",
               f"[{i}/{len(specs)}] rendering {label!r} on the character")
        ob: Dict[str, Any] = {"name": cs.name or label,
                              "views": list(spec.clothing_views),
                              "slots": made[-1]["slots"]}
        gref = made[-1].get("garment_ref")
        if gref:
            ob["garment_ref"] = gref
        _app("POST", f"/api/klein3/characters/{slug}/outfits", ob, timeout=180)

        def _outfit_done():
            o = _k3_job(slug, "outfit")
            _note_workers(jid, o, st)
            if o.get("status") == "error":
                raise Fatal(f"outfit render failed: {o.get('error')}")
            if o.get("detail"):
                _tick(jid, st, f"[{i}/{len(specs)}] {label}: {o['detail']}")
            return o if o.get("status") not in ("running", None) else None

        o = _wait(jid, _outfit_done, 5400, 15, f"outfit render {label!r}")
        made[-1]["outfit_job"] = {k: o.get(k) for k in ("status", "detail", "error")}
        st["costumes"] = made
    _stage(jid, st, "clothing", f"{len(made)} outfit(s) designed, adopted and worn")


def _stash_costume_refs(ref_ids: List[str]) -> List[str]:
    """Copy autogen-uploaded reference images into the COSTUME ref store.

    The two stores are separate on purpose — a character reference and a
    costume reference have different lifetimes — so a clothing photo uploaded
    to autogen has to be handed across rather than linked. Copied, not moved:
    a batch may reuse the same jacket photo for several characters.
    """
    import urllib.request
    out: List[str] = []
    for rid in (ref_ids or []):
        fp = _REF_DIR / f"{rid}.png"
        if not fp.exists():
            continue
        bound = uuid.uuid4().hex
        b = ("--" + bound).encode()
        payload = (b + b"\r\n"
                   + f'Content-Disposition: form-data; name="file"; '
                     f'filename="{fp.name}"\r\n'.encode()
                   + b"Content-Type: image/png\r\n\r\n" + fp.read_bytes()
                   + b"\r\n" + b + b"--\r\n")
        req = urllib.request.Request(
            "http://127.0.0.1:8899/api/costumes/refs", data=payload, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                got = json.loads(r.read().decode("utf-8", "replace"))
            if got.get("id"):
                out.append(got["id"])
        except Exception as e:                                   # noqa: BLE001
            logger.warning("autogen: costume ref upload failed: %s", e)
    return out


def _s_dataset(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """The LoRA dataset: plan, render, caption, QC, repair.

    This is `lora_train._autogen_pipeline`'s own recipe, driven from here so
    the toggles and the cancel apply to it. It is deliberately NOT a re-write:
    every call below is the same route that pipeline calls, in the same order.
    """
    _check_cancel(jid)
    trigger = (spec.trigger or "").strip() or _unique_trigger(slug)
    # ⚠ `desc` MUST be non-empty: `lora._norm_outfits` does `if not desc:
    # continue`, so an outfit with a name and a ref but no description is
    # silently dropped — the clothing stage would have spent ~7 renders per
    # outfit and contributed NOTHING to the dataset, with no error to see it by.
    outfits = []
    for c in (st.get("costumes") or []):
        if not c.get("garment_ref"):
            continue
        slots = c.get("slots") or {}
        desc = ", ".join(str(v).strip() for v in slots.values() if str(v).strip())
        outfits.append({"name": c.get("name") or "outfit",
                        "desc": desc or str(c.get("name") or "outfit"),
                        "kind": "named", "ref_id": c.get("garment_ref")})
    body = {"name": f"{slug}-auto", "char_slug": slug, "trigger": trigger,
            "class_token": spec.class_token, "target": "krea2",
            "preset": spec.preset, "count": int(spec.dataset_total)}
    if outfits:
        body["outfits"] = outfits
    _stage(jid, st, "dataset", f"planning {spec.dataset_total} images "
                               f"(trigger {trigger})")
    ds = _app("POST", "/api/lora/datasets", body, timeout=300)
    ds_id = ds.get("id")
    if not ds_id:
        raise RuntimeError("the dataset was not created")
    st["dataset"] = ds_id
    st["trigger"] = trigger
    total = len(ds.get("items") or []) or int(spec.dataset_total)

    _stage(jid, st, "dataset", f"rendering {total} images")
    g = _app("POST", f"/api/lora/datasets/{ds_id}/generate", {}, timeout=180)
    # ⚠ a no-op generate returns {"started": false} and never touches _RUNS, so
    # the status probe below would see run=None forever and time out after FOUR
    # HOURS on a dataset that was already complete.
    if not g.get("started"):
        _stage(jid, st, "dataset",
               f"nothing to render ({g.get('note') or 'already complete'})")
    else:

        def _rendered():
            d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
            run = d.get("run") or {}
            _note_workers(jid, run, st)
            if run.get("status") == "error":
                raise Fatal(f"dataset render failed: {run.get('error')}")
            _tick(jid, st, f"rendering {run.get('done', 0)}/{run.get('total', total)}"
                           + (f" across {len(run.get('workers') or [])} worker(s)"
                              if run.get("workers") else ""))
            return d if run.get("status") not in ("running", None) else None

        _wait(jid, _rendered, 4 * 3600, 20, "dataset render")

    _check_cancel(jid)
    _stage(jid, st, "dataset", "captioning")
    _app("POST", f"/api/lora/datasets/{ds_id}/caption", {"overwrite": True},
         timeout=1800)

    _check_cancel(jid)
    _stage(jid, st, "dataset", "QC pass")
    _app("POST", f"/api/lora/datasets/{ds_id}/qc", {"overwrite": True}, timeout=180)

    def _qc_done():
        d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
        run = d.get("run") or {}
        _note_workers(jid, run, st)
        _tick(jid, st, f"QC {run.get('done', 0)}/{run.get('total', total)}")
        return d if run.get("status") not in ("running", None) else None

    d = _wait(jid, _qc_done, 2 * 3600, 20, "dataset QC")
    flagged = int((d.get("flags") or {}).get("flagged") or 0)
    if flagged:
        _check_cancel(jid)
        _stage(jid, st, "dataset", f"{flagged} flagged — repair rounds")
        _app("POST", f"/api/lora/datasets/{ds_id}/repair",
             {"rounds": 2, "qc_after": True}, timeout=180)

        def _repaired():
            dd = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
            run = dd.get("run") or {}
            _note_workers(jid, run, st)
            _tick(jid, st, f"repair round {run.get('round', '?')}/"
                           f"{run.get('rounds', '?')} · "
                           f"{run.get('done', 0)}/{run.get('total', '?')}")
            return dd if run.get("status") not in ("running", None) else None

        try:
            _wait(jid, _repaired, 2 * 3600, 30, "dataset repair")
        except Cancelled:
            raise
        except Exception as e:                                   # noqa: BLE001
            # A repair that runs out of time is not a failed dataset — the
            # images are still there and still usable.
            _stage(jid, st, "dataset", f"repair did not finish ({e}) — continuing")

    _app("POST", f"/api/lora/datasets/{ds_id}/likeness", {}, timeout=1800)
    d = _app("GET", f"/api/lora/datasets/{ds_id}", timeout=60)
    st["dataset_flags"] = d.get("flags")
    _stage(jid, st, "dataset",
           f"dataset {ds_id} ready — {len(d.get('items') or [])} images, "
           f"{(d.get('flags') or {}).get('flagged', 0)} still flagged")


def _unique_trigger(slug: str) -> str:
    """A trigger no existing dataset is already using.

    ⚠ FOUND WHILE BUILDING THIS: `lora.py` derives the default trigger from the
    dataset NAME and validates nothing. Autogen names every dataset
    `<slug>-auto`, so running it twice on one character produced TWO datasets
    with DIFFERENT ids and the SAME trigger — two LoRAs answering to one word,
    and an ambiguous reverse-lookup in the installed-LoRA display. A suffix is
    cheap; discovering the collision after training is not.
    """
    base = "rbmn" + re.sub(r"[^a-z0-9]", "", slug.lower())[:8]
    try:
        used = {str(d.get("trigger") or "").lower()
                for d in (_app("GET", "/api/lora/datasets", timeout=60)
                          .get("datasets") or [])}
    except Exception:                                            # noqa: BLE001
        used = set()
    if base not in used:
        return base
    for n in range(2, 40):
        cand = f"{base}v{n}"
        if cand not in used:
            return cand
    return f"{base}{uuid.uuid4().hex[:4]}"


def _s_charsheet(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """The reference sheet. Free — PIL compositing, no GPU, no worker.

    Runs AFTER the dataset on purpose: each cell prefers a rendered dataset
    image matching its framing and angle and falls back to the tagged refs, so
    building it earlier produces a strictly poorer sheet for the same zero cost.
    """
    _check_cancel(jid)
    _stage(jid, st, "charsheet", "compositing the reference sheet (free)")
    r = _app("POST", "/api/charsheet/generate",
             {"slug": slug, "preset": spec.charsheet_preset, "labels": False},
             timeout=900)
    st["charsheet"] = {"url": r.get("url"), "missing": r.get("missing")}
    miss = r.get("missing") or []
    _stage(jid, st, "charsheet",
           "sheet built" + (f" — {len(miss)} empty cell(s): {miss}" if miss else ""))


def _s_lora(jid: str, st: dict, spec: AutogenSpec, slug: str) -> None:
    """Export, upload, train, score, install.

    Reuses `lora_train._train_pipeline` in-process rather than over HTTP —
    exactly as the existing autogen does — because it is already a blocking
    thread-safe function and this is already a thread.
    """
    # ⚠ HONESTY: this stage is the ONE place ⏹ stop cannot reach. Training runs
    # inside `_train_pipeline`, whose wait is a bare `while True: sleep(60)` on
    # the trainer helper with no cancel hook — so a cancel raised here would be
    # seen only when training finished, hours later. The check happens BEFORE
    # the pipeline starts, which is the last honest moment to stop.
    _check_cancel(jid)
    ds_id = st.get("dataset")
    if not ds_id:
        raise RuntimeError("no dataset to train on")
    from backend.api.lora_train import _TRAIN_DIR, _hj, _train_pipeline, _tsettings

    # ⚠⚠ v1.276.48 — PREFLIGHT THE TRAINER *HERE*, not just at the start.
    # `/autogen` checks the helper before it queues anything — but that check is
    # HOURS stale by the time the chain reaches training, and a box that was up
    # when you pressed the button can reboot, sleep or take a Windows Update in
    # between. That is exactly what happened to Lorenzo: the run died with a
    # bare `WinError 10060` after the dataset was already built.
    # Failing HERE costs nothing and says something useful, because the dataset
    # is finished and recorded — ↻ retry resumes at this stage rather than
    # re-rendering forty images.
    t = _tsettings()
    try:
        _hj("/health", None, None, 10.0)
    except Exception as e:                                       # noqa: BLE001
        raise RuntimeError(
            f"the trainer box is not answering, so training cannot start — "
            f"but NOTHING IS LOST: dataset {ds_id} is built and this job will "
            f"resume at the training stage. Wake {t['host']}:{t['port']} (or "
            f"restart its helper), then press ↻ retry. ({e})") from None

    _stage(jid, st, "lora", f"trainer {t['host']} is up — export → upload → "
                            f"train → score → install")
    _train_pipeline(ds_id, {})
    tr = _state_load(_TRAIN_DIR / f"{ds_id}.json")
    if tr.get("error"):
        raise RuntimeError(f"training: {tr['error']}")
    st["installed"] = tr.get("installed")
    st["pick"] = tr.get("pick")
    # ⚠ v1.276.52 — carry the EPOCH STORY across, not just the filename. These
    # live on the TRAIN state (`_train/<ds>.json`); the board reads the AUTOGEN
    # job, so without this copy it showed a filename and nothing else — and the
    # one thing worth knowing is whether the epoch you got is the epoch that
    # scored best. `install_note` is present ONLY when a substitution happened
    # (v1.276.49: the best epoch had no checkpoint file), so its absence is
    # itself the good news.
    for k in ("installed_epoch", "best_epoch", "install_note", "run_id"):
        if tr.get(k) is not None:
            st[k] = tr[k]
    pick = tr.get("pick") or {}
    if pick.get("best_score") is not None:
        st["best_score"] = pick["best_score"]
        st["epochs_scored"] = len(pick.get("scores") or [])
    sub = (" ⚠ " + tr["install_note"]) if tr.get("install_note") else ""
    _stage(jid, st, "lora",
           f"{tr.get('installed')} installed — usable in 🧬"
           + (f" · epoch {tr.get('installed_epoch')}" if tr.get("installed_epoch") else "")
           + (f", likeness {pick['best_score']:.4f}" if pick.get("best_score") else "")
           + sub)


# ── the runner ───────────────────────────────────────────────────────────────
_STAGE_FN: Dict[str, Any] = {
    "base": _s_base, "gate": _s_gate, "views": _s_views,
    "clothing": _s_clothing, "dataset": _s_dataset,
    "charsheet": _s_charsheet, "lora": _s_lora,
}


def _run_one(jid: str) -> None:
    """Run one character's chain to whatever point its toggles asked for."""
    st = _state_load(_fp(jid))
    if not st.get("spec"):
        # ⚠ a corrupt or truncated state file loads as {} and would explode on
        # AutogenSpec(**{}) — leaving a permanent red row nobody can explain.
        st["error"] = "state file has no spec — cannot run this job"
        _stage(jid, st, "error", st["error"])
        return
    spec = AutogenSpec(**st["spec"])
    _ACTIVE[jid] = True
    try:
        # ⚠ A cancel that arrived between this job being dequeued and _ACTIVE
        # being set would otherwise be LOST: the cancel route saw no active job,
        # wrote "cancelled" from its own copy of the state, and this thread then
        # carried on and overwrote the file — the screen said cancelled while
        # the GPUs kept working. Cancel now always sets the flag; this checks it
        # as its first act.
        _check_cancel(jid)
        slug = st.get("slug") or ""
        for stage in spec.stages():
            _check_cancel(jid)
            if stage in (st.get("completed") or []):
                # RESUME: a stage that finished before the restart is not
                # re-run. This is why `completed` is appended per stage rather
                # than inferred from `stage` — the current stage tells you
                # where it stopped, not what it had already achieved.
                _stage(jid, st, stage, "already done — skipping (resumed)")
                continue
            if stage == "character":
                slug = _s_character(jid, st, spec)
            else:
                if not slug:
                    raise RuntimeError("no character slug — the character "
                                       "stage did not run")
                _STAGE_FN[stage](jid, st, spec, slug)
            st.setdefault("completed", []).append(stage)
            _state_save(_fp(jid), st)
        _stage(jid, st, "done", "finished: " + ", ".join(spec.stages()))
    except Cancelled:
        _interrupt_workers(jid)
        st["error"] = None
        _stage(jid, st, "cancelled",
               f"cancelled during {st.get('stage')} — everything already "
               f"produced is kept")
    except Exception as e:                                       # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"
        # ⚠⚠ A JOB MUST ALWAYS REACH A TERMINAL STAGE. This write failing was
        # the difference between "the run failed and said so" and "the run
        # hung forever": when `_state_save` raised here (WinError 5, a reader
        # holding the file), the exception escaped, the job stayed at whatever
        # stage it died in, and every poller waited on it indefinitely. The
        # writer is hardened now; this is the second line of defence, because
        # the failure mode is silent and expensive.
        try:
            _stage(jid, st, "error", st["error"])
        except Exception:                                        # noqa: BLE001
            logger.exception("autogen %s: could not even record the failure", jid)
            try:
                time.sleep(0.5)
                _stage(jid, st, "error", st["error"])
            except Exception:                                    # noqa: BLE001
                pass
        logger.exception("autogen %s failed", jid)
    finally:
        _ACTIVE.pop(jid, None)
        _CANCEL.pop(jid, None)
        _WORKERS_TOUCHED.pop(jid, None)


# ── the queue ────────────────────────────────────────────────────────────────
# ⚠ STRICTLY SERIAL, and that is a decision rather than a simplification.
# Every stage already fans across all three boxes, and the costume design lane
# holds a single GLOBAL lock. Two characters in parallel would contend for the
# same GPUs, make both slower, and make the costume stage 409 unpredictably.
# One at a time is faster in wall-clock terms and far easier to read.

def _queue_read() -> List[str]:
    return list((_state_load(_QUEUE_FP) or {}).get("pending") or [])


def _queue_write(pending: List[str]) -> None:
    _state_save(_QUEUE_FP, {"pending": pending, "updated_at": _now()})


def _queue_push(jid: str) -> None:
    with _QUEUE_LOCK:
        q = _queue_read()
        if jid not in q:
            q.append(jid)
        _queue_write(q)


def _queue_paused() -> bool:
    try:
        return bool(json.loads(_PAUSE_FP.read_text("utf-8")).get("paused"))
    except Exception:                                            # noqa: BLE001
        return False


def _set_paused(paused: bool, note: str = "") -> None:
    _ensure_dirs()
    _state_save(_PAUSE_FP, {"paused": bool(paused), "at": _now(),
                            "note": note})


def _drain() -> None:
    """Run queued jobs one after another until the queue is empty.

    ⚠ THE EXIT IS THE DELICATE PART. `_ensure_drainer` guards on
    `_DRAINER.is_alive()`, which stays True for a moment after this function
    returns — so a `_queue_push` landing in that window would enqueue a job and
    then decline to start a drainer, and nothing else polls the queue. The job
    would sit at `queued` until some unrelated enqueue happened to revive it.
    Clearing `_DRAINER` *under the lock, at the moment we decide to exit* closes
    the window: a pusher either sees a live drainer that has not yet decided, or
    a cleared one it must restart.
    """
    global _DRAINER
    while True:
        with _QUEUE_LOCK:
            # ⏸ checked BEFORE popping, so a pause never interrupts the job
            # that is already rendering — it stops the NEXT one from starting.
            # The drainer exits; unpause (and any later push) restarts it.
            if _queue_paused():
                _DRAINER = None
                return
            q = _queue_read()
            if not q:
                _DRAINER = None
                return
            jid = q.pop(0)
            _queue_write(q)
        st = _state_load(_fp(jid))
        if not st:
            continue
        if st.get("stage") in ("done", "cancelled"):
            continue
        try:
            _run_one(jid)
        except Exception:                                        # noqa: BLE001
            # ⚠ ONE BAD CHARACTER MUST NOT EMPTY THE BATCH. _run_one already
            # records its own failure in the state file; this only stops the
            # drainer dying with it.
            logger.exception("autogen: job %s blew up outside its handler", jid)


def _ensure_drainer() -> None:
    """Start the drainer if one is not already running. Safe to call always."""
    global _DRAINER
    with _QUEUE_LOCK:
        if _DRAINER is not None and _DRAINER.is_alive():
            return
        _DRAINER = threading.Thread(target=_drain, daemon=True, name="autogen-drain")
        t = _DRAINER
    t.start()


def resume_on_startup() -> None:
    """Re-attach to a batch that a restart interrupted.

    State files always survived; nothing ever read them back. A job whose stage
    is neither terminal nor queued was RUNNING when the process died — it goes
    back on the front of the queue, and `completed` means it resumes at the
    stage it reached rather than from the beginning.
    """
    _ensure_dirs()
    try:
        pending = _queue_read()
        orphans = []
        for fp in sorted(_JOB_DIR.glob("*.json")):
            st = _state_load(fp)
            jid = fp.stem
            if st.get("stage") in ("done", "error", "cancelled"):
                continue
            if jid in pending:
                continue
            if not st.get("spec"):
                # a truncated or hand-edited file loads as {} — resuming it
                # would only produce a job that dies on its own state.
                logger.warning("autogen: %s has no spec, not resuming", jid)
                continue
            st["resumed_after_restart"] = _now()
            _state_save(fp, st)
            orphans.append(jid)
        if orphans or pending:
            _queue_write(orphans + pending)
            logger.info("autogen: resuming %d interrupted + %d queued",
                        len(orphans), len(pending))
            _ensure_drainer()
    except Exception:                                            # noqa: BLE001
        logger.exception("autogen: resume scan failed")


# ── routes ───────────────────────────────────────────────────────────────────
class BatchIn(BaseModel):
    characters: List[AutogenSpec]
    label: str = ""


@router.get("/health")
async def health():
    _ensure_dirs()
    return {"ok": True, "stages": STAGES,
            "queue": len(_queue_read()),
            "active": [k for k, v in _ACTIVE.items() if v],
            "drainer": bool(_DRAINER and _DRAINER.is_alive()),
            "paused": _queue_paused()}


@router.post("/estimate")
async def estimate_route(spec: AutogenSpec):
    """What this will cost, BEFORE you agree to it."""
    return estimate(spec)


@router.post("/refs")
async def upload_ref(file: UploadFile = File(...), kind: str = Form("character")):
    """Stash a reference image for a not-yet-existing character or costume.

    It cannot go straight onto the character because at upload time there IS no
    character — that is the whole point of describing a batch before running
    it. Files live here until a run consumes them, and are COPIED onward rather
    than moved so one photo can seed several characters in a batch.
    """
    _ensure_dirs()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    rid = uuid.uuid4().hex[:12]
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(raw)) as im:
            im.convert("RGB").save(_REF_DIR / f"{rid}.png", "PNG")
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(400, f"unreadable image: {e}")
    return {"id": rid, "kind": kind, "name": file.filename or f"{rid}.png",
            "url": f"/api/autogen/refs/{rid}/image"}


@router.get("/refs/{rid}/image")
async def ref_image(rid: str):
    from fastapi.responses import FileResponse
    fp = _REF_DIR / f"{rid}.png"
    if not fp.exists():
        raise HTTPException(404, "not found")
    return FileResponse(fp, media_type="image/png")


@router.post("/run")
async def run(spec: AutogenSpec):
    """Queue ONE character."""
    return _enqueue([spec], label=spec.name)


@router.post("/batch")
async def batch(body: BatchIn):
    """Queue MANY characters. They run strictly one after another."""
    if not body.characters:
        raise HTTPException(400, "no characters in the batch")
    return _enqueue(body.characters, label=body.label or
                    f"batch of {len(body.characters)}")


def _enqueue(specs: List[AutogenSpec], label: str) -> dict:
    _ensure_dirs()
    # ⚠ VALIDATE EVERYTHING FIRST. Validating inside the queueing loop meant a
    # bad character 5 left characters 1-4 already queued and running while the
    # client saw a 400 — a half-committed batch nobody asked for.
    for spec in specs:
        if not (spec.name or "").strip():
            raise HTTPException(400, "every character needs a name")
        if not spec.ref_ids and not (spec.description or "").strip():
            raise HTTPException(
                400, f"{spec.name!r}: give it reference images OR a description "
                     f"— there is nothing to build the character from otherwise")
    made = []
    batch_id = uuid.uuid4().hex[:8]
    for spec in specs:
        jid = f"{_slugify(spec.name)[:24]}-{uuid.uuid4().hex[:6]}"
        st = {"id": jid, "batch": batch_id, "label": label,
              "name": spec.name, "slug": _slugify(spec.name),
              "spec": json.loads(spec.model_dump_json()),
              "estimate": estimate(spec),
              "created_at": _now(), "error": None, "completed": []}
        # ⭐ Written BEFORE the job is queued and before any thread exists, so
        # the status a poller reads can never be ahead of the work (v1.276.29).
        _stage(jid, st, "queued", "waiting for the queue")
        _queue_push(jid)
        made.append({"id": jid, "name": spec.name, "stages": spec.stages()})
    _ensure_drainer()
    return {"started": True, "batch": batch_id, "jobs": made,
            "queue": len(_queue_read())}


@router.get("/jobs")
async def jobs(limit: int = 50):
    """Everything, newest first — the batch board."""
    _ensure_dirs()
    out = []
    for fp in _JOB_DIR.glob("*.json"):
        st = _state_load(fp)
        if not st:
            continue
        row = {k: st.get(k) for k in
               ("id", "batch", "label", "name", "slug", "stage", "detail",
                "error", "created_at", "updated_at", "dataset", "trigger",
                "installed", "completed", "estimate", "stage_times",
                "stage_started_at", "elapsed_s")}
        row["active"] = bool(_ACTIVE.get(st.get("id") or ""))
        row["queued"] = (st.get("id") or "") in _queue_read()
        row["elapsed_s"] = _live_elapsed(st, row["active"])
        row["log_lines"] = len(st.get("log") or [])
        # the list view carries the epoch story too, so the collapsed row can
        # show a likeness score without expanding
        _merge_train_facts(st)
        for k in ("installed_epoch", "best_epoch", "install_note",
                  "best_score", "epochs_scored"):
            row[k] = st.get(k)
        row["installed"] = row.get("installed") or st.get("installed")
        out.append(row)
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"jobs": out[:max(1, min(limit, 500))],
            "queue": _queue_read(),
            "running": [k for k, v in _ACTIVE.items() if v],
            "paused": _queue_paused()}


class PauseIn(BaseModel):
    paused: bool


@router.post("/queue/pause")
async def queue_pause(body: PauseIn):
    """⏸ Hold the queue AFTER the current job finishes — nothing new starts
    until unpaused. Persisted on disk, so it survives a backend restart: pause,
    reboot, fix whatever needed fixing, unpause, and the batch carries on."""
    _set_paused(body.paused)
    if not body.paused:
        _ensure_drainer()            # wake the queue back up
    return {"paused": _queue_paused(), "queue": len(_queue_read()),
            "running": [k for k, v in _ACTIVE.items() if v]}


def _merge_train_facts(st: dict) -> None:
    """Fill the epoch story from the TRAIN state if the job predates it.

    ⚠ v1.276.52 — `_s_lora` now copies these onto the job as it finishes, but
    runs that completed BEFORE that change have the facts only in
    `_train/<ds>.json`. Reading them here rather than migrating means the two
    already-finished runs display correctly, a job whose training is re-attached
    later picks the new numbers up, and there is no migration to get wrong.
    Read-only and best-effort: the job file is the record, this only fills gaps.
    """
    ds_id = st.get("dataset")
    if not ds_id or st.get("installed_epoch") is not None:
        return
    try:
        from backend.api.lora_train import _TRAIN_DIR
        tr = _state_load(_TRAIN_DIR / f"{ds_id}.json")
    except Exception:                                            # noqa: BLE001
        return
    if not tr:
        return
    for k in ("installed_epoch", "best_epoch", "install_note", "run_id"):
        if st.get(k) is None and tr.get(k) is not None:
            st[k] = tr[k]
    pick = tr.get("pick") or {}
    if st.get("best_score") is None and pick.get("best_score") is not None:
        st["best_score"] = pick["best_score"]
        st["epochs_scored"] = len(pick.get("scores") or [])
    if st.get("installed") is None and tr.get("installed"):
        st["installed"] = tr["installed"]


def _live_elapsed(st: dict, active: bool) -> float:
    """Seconds this job has been running.

    ⚠ `elapsed_s` in the file is only as fresh as the last WRITE, and a stage
    can sit quiet for minutes between ticks — so for a job that is actually
    running it is computed against the wall clock instead. A frozen timer on a
    live run is exactly the "is this thing stuck?" question the timer exists to
    answer, so it must not lie by omission.
    """
    t0 = st.get("t0")
    if active and t0:
        try:
            return round(time.time() - float(t0), 1)
        except Exception:                                        # noqa: BLE001
            pass
    return float(st.get("elapsed_s") or 0.0)


@router.get("/jobs/{jid}")
async def job(jid: str, log: int = 200):
    """The whole record. `?log=` caps the returned log (0 = none, -1 = all)."""
    st = _state_load(_fp(jid))
    if not st:
        raise HTTPException(404, "unknown job")
    st["active"] = bool(_ACTIVE.get(jid))
    st["queued"] = jid in _queue_read()
    st["elapsed_s"] = _live_elapsed(st, st["active"])
    st["elapsed_human"] = _human(int(st["elapsed_s"]))
    _merge_train_facts(st)
    # the stage currently in flight has no closed duration yet — publish it live
    if st["active"] and st.get("_stage_t0"):
        try:
            st["stage_elapsed_s"] = round(time.time() - float(st["_stage_t0"]), 1)
        except Exception:                                        # noqa: BLE001
            pass
    full = st.get("log") or []
    if log == 0:
        st["log"] = []
    elif log > 0:
        st["log"] = full[-log:]
    st["log_total"] = len(full)
    st.pop("_stage_t0", None)          # internal bookkeeping, not for the UI
    return st


@router.post("/jobs/{jid}/cancel")
async def cancel(jid: str):
    """Stop it. Really stop it.

    Sets the flag the pipeline checks between stages AND inside every wait, and
    interrupts the workers this run touched so the render in flight stops too.
    A queued-but-not-started job is simply removed from the queue.
    """
    st = _state_load(_fp(jid))
    if not st:
        raise HTTPException(404, "unknown job")
    if st.get("stage") in ("done", "error", "cancelled"):
        return {"ok": True, "note": f"already {st.get('stage')}", "stage": st.get("stage")}
    with _QUEUE_LOCK:
        q = [x for x in _queue_read() if x != jid]
        _queue_write(q)
    # ⚠ SET THE FLAG UNCONDITIONALLY. Setting it only when `_ACTIVE` was true
    # lost every cancel that arrived in the window between a job being dequeued
    # and its thread setting `_ACTIVE` — the route wrote "cancelled" from its
    # own copy of the state and the thread then ran on and overwrote it, so the
    # screen said cancelled while the GPUs kept going. The flag is cheap, the
    # runner clears it in its `finally`, and it is checked as the runner's very
    # first act.
    _CANCEL[jid] = True
    if _ACTIVE.get(jid):
        import asyncio
        await asyncio.to_thread(_interrupt_workers, jid)
        return {"ok": True, "note": "cancelling — the current step is being "
                                    "interrupted", "stage": st.get("stage")}
    _stage(jid, st, "cancelled", "removed from the queue before it started")
    return {"ok": True, "note": "removed from the queue", "stage": "cancelled"}


@router.post("/jobs/{jid}/retry")
async def retry(jid: str):
    """Put a failed or cancelled job back on the queue.

    It resumes at the stage it reached — `completed` survives, so a run that
    died at `dataset` does not re-render the views it already has.
    """
    st = _state_load(_fp(jid))
    if not st:
        raise HTTPException(404, "unknown job")
    if _ACTIVE.get(jid):
        raise HTTPException(409, "that job is running")
    st["error"] = None
    # ⚠ clear a stale cancel flag. If a cancel landed between the runner's
    # terminal write and its `finally`, the flag survives — and the retry would
    # raise Cancelled on its very first check, looking like the retry button
    # does not work.
    _CANCEL.pop(jid, None)
    _stage(jid, st, "queued", f"re-queued (keeping {len(st.get('completed') or [])} "
                              f"completed stage(s))")
    _queue_push(jid)
    _ensure_drainer()
    return {"ok": True, "queue": len(_queue_read())}


@router.post("/jobs/{jid}/delete")
async def delete(jid: str):
    if _ACTIVE.get(jid):
        raise HTTPException(409, "cancel it before deleting it")
    with _QUEUE_LOCK:
        _queue_write([x for x in _queue_read() if x != jid])
    fp = _fp(jid)
    if fp.exists():
        fp.unlink()
    return {"deleted": jid}


@router.post("/queue/clear")
async def queue_clear():
    """Empty the PENDING queue. Does not touch whatever is running — use
    cancel for that, so 'clear the queue' can never mean 'kill the render
    that is 80% done'."""
    with _QUEUE_LOCK:
        n = len(_queue_read())
        for jid in _queue_read():
            st = _state_load(_fp(jid))
            if st and st.get("stage") == "queued":
                _stage(jid, st, "cancelled", "queue cleared")
        _queue_write([])
    return {"cleared": n}
