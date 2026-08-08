"""Offline mock test for v1.204 pose tag/move logic — no FastAPI app, no worker.

Extracts the new functions from klein2.py source (AST) and runs them against a
temp pose store with stubbed _read_poses/_write_poses/_ensure_set.
"""
import ast, asyncio, json, sys, tempfile
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/klein2.py").read_text("utf-8")
tree = ast.parse(SRC)
WANT = {"_norm_tags", "poses_bulk_move", "poses_bulk_tags", "poses_bulk_delete", "pose_update"}
CLASSES = {"PoseUpdateIn", "PoseBulkMoveIn", "PoseBulkTagsIn", "PoseIdsIn"}
chunks = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.ClassDef) and node.name in CLASSES:
        chunks.append(ast.unparse(node))
assert len(chunks) == len(WANT) + len(CLASSES), f"extracted {len(chunks)}"

tmp = Path(tempfile.mkdtemp())
POSES = tmp / "poses"; POSES.mkdir()
STORE = {"items": []}
SETS = {"names": []}


class HTTPException(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


class Box:                      # stand-in for the pydantic bodies
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def __getattr__(self, _name):   # fields added by later versions default to None
        return None


def _now_iso():
    return "2026-08-03T00:00:00Z"


def _read_poses():
    return json.loads(json.dumps(STORE["items"]))


def _write_poses(items):
    STORE["items"] = json.loads(json.dumps(items))


def _ensure_set(name):
    if name and name not in SETS["names"]:
        SETS["names"].append(name)


ns = {"List": list, "Optional": object, "Any": object, "BaseModel": object,
      "HTTPException": HTTPException, "_now_iso": _now_iso, "_read_poses": _read_poses,
      "_write_poses": _write_poses, "_ensure_set": _ensure_set, "_K2_POSES": POSES,
      "asyncio": asyncio, "random": __import__("random"),
      "_pose_public": lambda it: dict(it)}
from uuid import uuid4
ns["uuid4"] = uuid4
code = "from __future__ import annotations\n\n" + "\n\n".join(
    c for c in chunks if not c.startswith("class "))
exec(code, ns)
norm = ns["_norm_tags"]
bulk_move = ns["poses_bulk_move"]
bulk_tags = ns["poses_bulk_tags"]
bulk_del = ns["poses_bulk_delete"]
pose_update = ns["pose_update"]
run = asyncio.get_event_loop().run_until_complete
fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


# ── _norm_tags ────────────────────────────────────────────────────────────
check("norm: string split + trim", norm(" Standing ; Kneeling,Action ") == ["Standing", "Kneeling", "Action"], norm(" Standing ; Kneeling,Action "))
check("norm: case-insensitive dedupe keeps first", norm(["Standing", "standing", "STANDING"]) == ["Standing"])
check("norm: 8-tag cap", len(norm([f"t{i}" for i in range(20)])) == 8)
check("norm: 32-char cap", len(norm(["x" * 99])[0]) == 32)
check("norm: empties dropped", norm([" ", "", None, "ok"]) == ["ok"], norm([" ", "", None, "ok"]))

# ── fixture: 3 poses in 'Bodybuilding', 1 in 'Defaults' ───────────────────
def seed():
    STORE["items"] = [
        {"id": "a1", "name": "Front double biceps", "set": "Bodybuilding", "category": "Bodybuilding",
         "tags": ["Standing"], "prompt": "p1", "source": "generated"},
        {"id": "a2", "name": "Side chest", "set": "Bodybuilding", "category": "Bodybuilding",
         "tags": ["Standing"], "prompt": "p2", "source": "generated"},
        {"id": "a3", "name": "Kneeling flex", "set": "Bodybuilding", "category": "Bodybuilding",
         "tags": ["Kneeling"], "prompt": "p3", "source": "generated"},
        {"id": "b1", "name": "Side chest", "set": "Defaults", "category": "Defaults",
         "tags": [], "prompt": "p4", "source": "generated"},
    ]
    SETS["names"][:] = ["Bodybuilding", "Defaults"]
    for pid in ("a1", "a2", "a3", "b1"):
        (POSES / f"{pid}.png").write_bytes(b"PNG" + pid.encode())


seed()

# ── pose_update: MOVE (the v1.204 bug fix) ────────────────────────────────
r = run(pose_update("a3", Box(name=None, set="Low poses", category=None, tags=None,
                              prompt=None, regenerate=False, seed=None), None))
rec = next(i for i in STORE["items"] if i["id"] == "a3")
check("update: set moves the pose", rec["set"] == "Low poses")
check("update: category mirrors set", rec["category"] == "Low poses")
check("update: new set auto-registered", "Low poses" in SETS["names"])
check("update: tags untouched", rec["tags"] == ["Kneeling"])

# legacy alias still works
run(pose_update("a3", Box(name=None, set=None, category="Bodybuilding", tags=None,
                          prompt=None, regenerate=False, seed=None), None))
check("update: legacy `category` alias moves too",
      next(i for i in STORE["items"] if i["id"] == "a3")["set"] == "Bodybuilding")

# tags replacement + normalisation through the route
run(pose_update("a1", Box(name=None, set=None, category=None,
                          tags=["Standing", "standing", " Front ", ""],
                          prompt=None, regenerate=False, seed=None), None))
check("update: tags normalised on save",
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"] == ["Standing", "Front"],
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"])

# empty set string must NOT wipe the set
run(pose_update("a1", Box(name=None, set="   ", category=None, tags=None,
                          prompt=None, regenerate=False, seed=None), None))
check("update: blank set is ignored",
      next(i for i in STORE["items"] if i["id"] == "a1")["set"] == "Bodybuilding")

# ── bulk MOVE ─────────────────────────────────────────────────────────────
seed()
r = run(bulk_move(Box(ids=["a1", "a2"], set="Contest", copy=False)))
check("move: counts", r["moved"] == 2 and r["copied"] == 0 and r["missing"] == 0, r)
check("move: target set created", "Contest" in SETS["names"])
check("move: records carry new set+category",
      all(i["set"] == "Contest" and i["category"] == "Contest"
          for i in STORE["items"] if i["id"] in ("a1", "a2")))
check("move: untouched pose stays", next(i for i in STORE["items"] if i["id"] == "a3")["set"] == "Bodybuilding")
check("move: images untouched", (POSES / "a1.png").exists())

# name collision inside the target set is disambiguated, not dropped
r = run(bulk_move(Box(ids=["b1"], set="Contest", copy=False)))
names = sorted(i["name"] for i in STORE["items"] if i["set"] == "Contest")
check("move: collision disambiguated", names == ["Front double biceps", "Side chest", "Side chest (2)"], names)
check("move: nothing lost", len(STORE["items"]) == 4)

# moving into the set it's already in is a no-op, not an error
r = run(bulk_move(Box(ids=["a1"], set="Contest", copy=False)))
check("move: same-set no-op", r["moved"] == 0 and len(STORE["items"]) == 4, r)

# missing ids are counted, not fatal
r = run(bulk_move(Box(ids=["nope"], set="Contest", copy=False)))
check("move: missing id counted", r["missing"] == 1, r)

# ── bulk COPY ─────────────────────────────────────────────────────────────
seed()
r = run(bulk_move(Box(ids=["a1", "a3"], set="Defaults", copy=True)))
check("copy: counts", r["copied"] == 2 and r["moved"] == 0, r)
check("copy: originals stay put",
      all(next(i for i in STORE["items"] if i["id"] == p)["set"] == "Bodybuilding" for p in ("a1", "a3")))
copies = [i for i in STORE["items"] if i["set"] == "Defaults" and i["id"] not in ("b1",)]
check("copy: 2 new records", len(copies) == 2, [c["name"] for c in copies])
check("copy: new ids", all(c["id"] not in ("a1", "a3") for c in copies))
check("copy: image files duplicated",
      all((POSES / f"{c['id']}.png").exists() for c in copies))
check("copy: image bytes match source",
      (POSES / f"{copies[0]['id']}.png").read_bytes() in (b"PNGa1", b"PNGa3"))
check("copy: tags carried", any(c["tags"] == ["Standing"] for c in copies))

# ── bulk TAGS ─────────────────────────────────────────────────────────────
seed()
r = run(bulk_tags(Box(ids=["a1", "a2"], add=["Contest", "contest", "Flex"], remove=None, replace=None)))
check("tags: add counts", r["updated"] == 2, r)
check("tags: added + deduped",
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"] == ["Standing", "Contest", "Flex"],
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"])
check("tags: unselected pose untouched",
      next(i for i in STORE["items"] if i["id"] == "a3")["tags"] == ["Kneeling"])
run(bulk_tags(Box(ids=["a1"], add=None, remove=["standing"], replace=None)))
check("tags: remove is case-insensitive",
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"] == ["Contest", "Flex"],
      next(i for i in STORE["items"] if i["id"] == "a1")["tags"])
run(bulk_tags(Box(ids=["a1"], add=None, remove=None, replace=["Only"])))
check("tags: replace wins", next(i for i in STORE["items"] if i["id"] == "a1")["tags"] == ["Only"])
run(bulk_tags(Box(ids=["a1"], add=None, remove=None, replace=[])))
check("tags: replace [] clears", next(i for i in STORE["items"] if i["id"] == "a1")["tags"] == [])
check("tags: set never changed by tagging",
      next(i for i in STORE["items"] if i["id"] == "a1")["set"] == "Bodybuilding")

# ── bulk DELETE ───────────────────────────────────────────────────────────
seed()
r = run(bulk_del(Box(ids=["a1", "a2", "ghost"])))
check("delete: count", r["deleted"] == 2, r)
check("delete: records gone", {i["id"] for i in STORE["items"]} == {"a3", "b1"})
check("delete: files unlinked", not (POSES / "a1.png").exists() and not (POSES / "a2.png").exists())
check("delete: survivor file kept", (POSES / "a3.png").exists())

# ── guards ────────────────────────────────────────────────────────────────
for label, fn, box in (
    ("move: empty ids -> 400", bulk_move, Box(ids=[], set="X", copy=False)),
    ("move: empty set -> 400", bulk_move, Box(ids=["a3"], set="  ", copy=False)),
    ("tags: empty ids -> 400", bulk_tags, Box(ids=[], add=["x"], remove=None, replace=None)),
    ("delete: empty ids -> 400", bulk_del, Box(ids=[])),
):
    try:
        run(fn(box))
        check(label, False, "no exception raised")
    except HTTPException as e:
        check(label, e.code == 400, e)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
