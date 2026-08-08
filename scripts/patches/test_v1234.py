"""v1.234 test — head-yaw angle verdicts, and the route that writes them.

The bug this suite exists to prevent is the one v1.219 taught: testing a
function the route never calls the way the route calls it.  So the angle route
is invoked as FastAPI invokes it, against a real dataset.json on disk, with
`likeness.pose` stubbed to replay the 40 real measurements from
`scripts/_diag/yaw.json`.

    venv\\Scripts\\python.exe scripts\\patches\\test_v1234.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


# ── the 40 real measurements, so the test is against real data ───────────────
# id, planned angle, yaw, det_score, kps_yaw, faces
#
# v1.242 adds FRAMING, from `framing_probe` over the same 40 images:
#   FR[id] = (planned framing, face height / image height, face centre y)
REAL = [
    ("0001", "front", None, None, None, 0),
    ("0002", "three_quarter_left", -19.3, 0.82, -0.460, 1),
    ("0003", "profile_left", -60.9, 0.76, -2.431, 1),
    ("0004", "front", 2.0, 0.75, 0.051, 1),
    ("0005", "three_quarter_right", -9.5, 0.84, -0.361, 1),
    ("0006", "profile_right", 56.5, 0.76, 2.947, 1),
    ("0007", "three_quarter_left", 24.2, 0.646, 0.783, 1),
    ("0008", "three_quarter_left", -20.1, 0.68, -0.589, 1),
    ("0009", "three_quarter_right", 36.8, 0.82, 1.003, 1),
    ("0010", "front", 2.2, 0.89, 0.014, 1),
    ("0011", "front", 1.2, 0.84, 0.032, 1),
    ("0012", "three_quarter_left", -8.1, 0.88, -0.276, 1),
    ("0013", "profile_left", -63.9, 0.79, -2.854, 1),
    ("0014", "front", 1.6, 0.89, 0.031, 1),
    ("0015", "three_quarter_right", 30.8, 0.86, 0.840, 1),
    ("0016", "profile_right", 79.1, 0.75, 4.208, 1),
    ("0017", "back", None, None, None, 0),
    ("0018", "back", None, None, None, 0),
    ("0019", "three_quarter_right", -6.1, 0.84, -0.154, 1),
    ("0020", "front", 3.0, 0.88, 0.061, 1),
    ("0021", "front", 13.6, 0.57, 3.567, 1),
    ("0022", "three_quarter_left", -20.6, 0.84, -0.426, 1),
    ("0023", "profile_left", -70.1, 0.77, -3.240, 1),
    ("0024", "front", 2.6, 0.86, 0.051, 1),
    ("0025", "three_quarter_right", 27.7, 0.86, 0.563, 1),
    ("0026", "profile_right", 82.5, 0.75, 4.370, 1),
    ("0027", "three_quarter_left", -22.1, 0.80, -0.545, 1),
    ("0028", "back", None, None, None, 0),
    ("0029", "three_quarter_right", 21.3, 0.86, 0.433, 1),
    ("0030", "front", 5.4, 0.91, 0.093, 1),
    ("0031", "front", 2.0, 0.88, 0.052, 1),
    ("0032", "three_quarter_left", -17.7, 0.91, -0.426, 1),
    ("0033", "profile_left", -78.3, 0.82, -7.523, 1),
    ("0034", "front", 0.5, 0.85, 0.031, 1),
    ("0035", "three_quarter_right", 4.3, 0.88, 0.181, 1),
    ("0036", "profile_right", 81.8, 0.79, 3.469, 1),
    ("0037", "three_quarter_left", -18.7, 0.85, -0.356, 1),
    ("0038", "back", None, None, None, 0),
    ("0039", "three_quarter_right", -8.2, 0.89, -0.214, 1),
    ("0040", "front", 1.8, 0.89, 0.069, 1),
]


FR = {
    "0001": ("face", None, None),        "0002": ("face", 0.6991, 0.4968),
    "0003": ("face", 0.6837, 0.4445),    "0004": ("face", 0.6560, 0.4655),
    "0005": ("face", 0.6443, 0.4895),    "0006": ("face", 0.7138, 0.4538),
    "0007": ("face", 0.6736, 0.4762),    "0008": ("face", 0.6923, 0.4478),
    "0009": ("headshot", 0.4704, 0.3472), "0010": ("headshot", 0.4731, 0.3607),
    "0011": ("headshot", 0.2955, 0.3245), "0012": ("headshot", 0.4633, 0.3947),
    "0013": ("headshot", 0.3892, 0.3228), "0014": ("headshot", 0.5070, 0.4006),
    "0015": ("headshot", 0.5099, 0.3758), "0016": ("headshot", 0.3815, 0.2983),
    "0017": ("upper", None, None),       "0018": ("upper", None, None),
    "0019": ("upper", 0.1941, 0.2513),   "0020": ("upper", 0.1436, 0.1804),
    "0021": ("upper", 0.2301, 0.8661),   "0022": ("upper", 0.2150, 0.3191),
    "0023": ("upper", 0.1992, 0.1658),   "0024": ("upper", 0.2224, 0.2023),
    "0025": ("upper", 0.1932, 0.1818),   "0026": ("upper", 0.2227, 0.1994),
    "0027": ("upper", 0.1788, 0.3109),   "0028": ("upper", None, None),
    "0029": ("full", 0.0974, 0.3087),    "0030": ("full", 0.0997, 0.2404),
    "0031": ("full", 0.1190, 0.1924),    "0032": ("full", 0.1075, 0.2183),
    "0033": ("full", 0.1211, 0.1857),    "0034": ("full", 0.1182, 0.1887),
    "0035": ("full", 0.1154, 0.2287),    "0036": ("full", 0.1021, 0.2534),
    "0037": ("full", 0.0916, 0.3569),    "0038": ("full", None, None),
    "0039": ("full", 0.1195, 0.1927),    "0040": ("full", 0.1218, 0.1982),
}


def pv_of(row):
    _id, _ang, yaw, det, kps, faces = row
    if faces == 0:
        return None
    _fr, _r, _cy = FR[_id]
    return {"faces": faces, "yaw": yaw, "pitch": 0.0, "roll": 0.0,
            "kps_yaw": kps, "det_score": det,
            "face_h_ratio": _r, "face_cy": _cy}


def main() -> int:
    from backend.services import likeness as lk

    print("\n== bands and sign ==")
    check("negative yaw means left",
          lk.ANGLE_BANDS["profile_left"][1] < 0 < lk.ANGLE_BANDS["profile_right"][0])
    check("three-quarter floor is 20 degrees",
          lk.ANGLE_BANDS["three_quarter_right"][0] == 20.0,
          str(lk.ANGLE_BANDS["three_quarter_right"]))
    check("back has no band (absence is the measurement)",
          "back" not in lk.ANGLE_BANDS)

    print("\n== angle_verdict against the 40 real measurements ==")
    got = {r[0]: lk.angle_verdict(r[1], pv_of(r)) for r in REAL}

    # The rows whose angle is not a matter of opinion.  If the instrument gets
    # these wrong there is no point discussing the three-quarters.
    for iid in ("0017", "0018", "0028", "0038"):
        check(f"{iid} back -> ok (no face is the right answer)", got[iid][0] is True,
              got[iid][1])
    for iid in ("0003", "0013", "0023", "0033"):
        check(f"{iid} profile_left -> ok", got[iid][0] is True, got[iid][1])
    for iid in ("0006", "0016", "0026", "0036"):
        check(f"{iid} profile_right -> ok", got[iid][0] is True, got[iid][1])
    fronts = ["0004", "0010", "0011", "0014", "0020", "0024",
              "0030", "0031", "0034", "0040"]
    check("all 10 well-detected fronts pass",
          all(got[i][0] is True for i in fronts),
          str([i for i in fronts if got[i][0] is not True]))

    # The judgement call, held to what my eyes said BEFORE the numbers existed.
    check("0035 (+4.3, looked front) -> miss on a 3/4-right row", got["0035"][0] is False)
    check("0039 (-8.2, head front) -> miss on a 3/4-right row", got["0039"][0] is False)
    check("0032 (-17.7, looked front) -> miss", got["0032"][0] is False)
    check("0037 (-18.7, looked front) -> miss", got["0037"][0] is False)
    check("0029 (+21.3, looked turned) -> ok", got["0029"][0] is True)

    # Bad fits are unmeasured, never failed.
    # v1.238: the guard is now the CONSISTENCY of the two measures. 0021 stays
    # rejected (yaw +13.6 says nearly front, keypoints 3.567 say full profile —
    # ratio 3.8 against a real-world range of 10.4 to 52.2). 0007 is now
    # ACCEPTED: yaw +24.2 and keypoints 0.783 agree, ratio 30.9, and its only
    # sin was a det score three thousandths under the old floor.
    check("0021 (yaw and keypoints disagree about how far) -> unmeasured",
          got["0021"][0] is None, got["0021"][1])
    check("0021's reason names the ratio, not the det score",
          "ratio" in got["0021"][1], got["0021"][1])
    check("v1.238: 0007 (det 0.646, but the two measures agree) -> MEASURED",
          got["0007"][0] is False, got["0007"][1])
    check("0001 (no face on a front row) -> unmeasured",
          got["0001"][0] is None, got["0001"][1])

    # A face on a back row is the one way a back shot fails.
    check("a face found on a back row -> miss",
          lk.angle_verdict("back", {"faces": 1, "yaw": 3.0, "kps_yaw": 0.05,
                                    "det_score": 0.9})[0] is False)

    print("\n== sign-disagreement guard ==")
    check("yaw and keypoints pointing opposite ways -> not confident",
          lk.angle_confident({"yaw": 30.0, "kps_yaw": -0.8, "det_score": 0.9}) is False)
    check("agreeing signs with a good detection -> confident",
          lk.angle_confident({"yaw": 30.0, "kps_yaw": 0.8, "det_score": 0.9}) is True)
    check("yaw and keypoints disagreeing on MAGNITUDE -> not confident",
          lk.angle_confident({"yaw": 13.6, "kps_yaw": 3.567, "det_score": 0.9}) is False)
    check("a faint face is still rejected",
          lk.angle_confident({"yaw": 30.0, "kps_yaw": 0.8, "det_score": 0.3}) is False)
    check("near-front yaw skips the ratio check rather than failing it",
          lk.angle_confident({"yaw": 2.0, "kps_yaw": 0.051, "det_score": 0.75}) is True)
    check("0021 is the ONLY one of the 40 the guard rejects",
          [r[0] for r in REAL
           if pv_of(r) is not None and not lk.angle_confident(pv_of(r))] == ["0021"],
          str([r[0] for r in REAL
               if pv_of(r) is not None and not lk.angle_confident(pv_of(r))]))

    print("\n== v1.243: the dataset calibrates its own framing bands ==")
    CAL = lk.framing_calibrate([(FR[r[0]][0], FR[r[0]][1]) for r in REAL
                                if FR[r[0]][1] is not None])
    print(f"    medians: {CAL['medians']}")
    print(f"    counts:  {CAL['n']}")
    print(f"    spacing: {CAL['separation']}")
    check("medians come out in the right order", CAL["order_ok"] is True)
    check("no dataset-level warnings on a healthy set",
          CAL["warnings"] == [], str(CAL["warnings"]))
    check("a median per shot type, from this dataset alone",
          set(CAL["medians"]) == {"face", "headshot", "upper", "full"})
    # 40 images, minus the 4 back rows with no face, minus 0001 — the face crop
    # with no face in it. A calibration must not be computed from images it
    # could not measure.
    check("calibration rests on the 35 images that HAVE a measurable face",
          sum(CAL["n"].values()) == 35, str(sum(CAL["n"].values())))

    # The point of the whole version: a different character does not need
    # re-tuning. Same shapes, every face height halved.
    HALF = lk.framing_calibrate([(FR[r[0]][0], FR[r[0]][1] / 2) for r in REAL
                                 if FR[r[0]][1] is not None])
    small = {r[0]: lk.framing_verdict(FR[r[0]][0], r[1],
                                      {"face_h_ratio": FR[r[0]][1] / 2,
                                       "face_cy": FR[r[0]][2], "faces": 1}, HALF)[0]
             for r in REAL if FR[r[0]][1] is not None}
    _failed = sorted(i for i, v in small.items() if v is not True)
    # 0021 must STILL fail: its defect is WHERE the face sits, which no amount of
    # re-calibration should forgive. Everything else must pass untouched.
    check("a character with a head half the size still passes, with NO re-tuning",
          _failed == ["0021"], f"failed: {_failed}")
    check("...and the genuinely broken image still fails on the smaller head",
          small["0021"] is False)
    check("...and the ABSOLUTE bands would have failed almost all of them",
          sum(1 for r in REAL if FR[r[0]][1] is not None
              and lk.framing_verdict(FR[r[0]][0], r[1],
                                     {"face_h_ratio": FR[r[0]][1] / 2,
                                      "face_cy": FR[r[0]][2], "faces": 1})[0] is False) > 20,
          "this is what v1.242 would have done to character two")

    check("a shot type with too few images falls back, and SAYS it fell back",
          "default bands" in lk.framing_verdict(
              "face", "front", {"face_h_ratio": 0.67, "face_cy": 0.45, "faces": 1},
              lk.framing_calibrate([("face", 0.68), ("face", 0.66)]))[1])
    check("out-of-order medians are reported as a dataset problem",
          lk.framing_calibrate(
              [("face", 0.10)] * 4 + [("full", 0.60)] * 4)["order_ok"] is False)
    check("shot types too close together are reported",
          any("apart" in w for w in lk.framing_calibrate(
              [("upper", 0.20)] * 4 + [("full", 0.18)] * 4)["warnings"]))

    print("\n== framing verdicts against the real 40 ==")
    fg = {r[0]: lk.framing_verdict(FR[r[0]][0], r[1], pv_of(r), CAL) for r in REAL}
    n_ok = sum(1 for v in fg.values() if v[0] is True)
    n_miss = sum(1 for v in fg.values() if v[0] is False)
    n_unm = sum(1 for v in fg.values() if v[0] is None)
    check("34 correct, 2 wrong, 4 unmeasured over the real 40",
          (n_ok, n_miss, n_unm) == (34, 2, 4), f"{n_ok}/{n_miss}/{n_unm}")
    check("the 4 unmeasured are exactly the 4 BACK rows",
          sorted(i for i, v in fg.items() if v[0] is None)
          == ["0017", "0018", "0028", "0038"],
          str(sorted(i for i, v in fg.items() if v[0] is None)))
    # The two the probe found, which every previous instrument called
    # "unmeasurable" and therefore passed.
    check("0001 — a face crop with NO FACE — fails", fg["0001"][0] is False, fg["0001"][1])
    check("0021 — face 87% down the frame — fails", fg["0021"][0] is False, fg["0021"][1])
    check("0021's reason names the position, not the size",
          "down the frame" in fg["0021"][1], fg["0021"][1])
    check("a missing face on a BACK row is unmeasured, never failed",
          fg["0017"][0] is None and fg["0038"][0] is None)
    check("every face crop that has a face passes",
          all(fg[i][0] is True for i in
              ("0002", "0003", "0004", "0005", "0006", "0007", "0008")))
    check("every full-body row with a face passes",
          all(fg[i][0] is True for i in
              ("0029", "0030", "0031", "0032", "0033", "0034", "0035", "0036",
               "0037", "0039", "0040")))
    # A shot rendered as the wrong type has to be named as such, or the message
    # is not actionable.
    _v, _w = lk.framing_verdict("full", "front",
                                {"face_h_ratio": 0.47, "face_cy": 0.35, "faces": 1}, CAL)
    check("a full-body row that renders as a headshot says how far off it is",
          _v is False and "4.1x" in _w and "BIGGER" in _w, _w)
    _v, _w = lk.framing_verdict("face", "front",
                                {"face_h_ratio": 0.115, "face_cy": 0.22, "faces": 1}, CAL)
    check("a face row that renders as a full body says how far off it is",
          _v is False and "SMALLER" in _w, _w)
    check("the 2x fence passes the widest REAL deviation (a 0.63x headshot)",
          lk.framing_verdict("headshot", "front",
                             {"face_h_ratio": 0.2955, "face_cy": 0.3245, "faces": 1},
                             CAL)[0] is True)
    # Stated as a known limit rather than left to be discovered.
    check("KNOWN LIMIT: 2x cannot separate upper from full",
          lk.framing_verdict("upper", "front",
                             {"face_h_ratio": CAL["medians"]["full"],
                              "face_cy": 0.25, "faces": 1}, CAL)[0] is True,
          "their medians are 1.73x apart; a person mask is the fix")
    check("the fallback bands do not overlap",
          lk.FRAMING_BANDS["face"][0] >= lk.FRAMING_BANDS["headshot"][1]
          and lk.FRAMING_BANDS["headshot"][0] >= lk.FRAMING_BANDS["upper"][1]
          and lk.FRAMING_BANDS["upper"][0] >= lk.FRAMING_BANDS["full"][1])

    print("\n== the ROUTE, called the way FastAPI calls it ==")
    from backend.api import lora

    tmp = Path(tempfile.mkdtemp(prefix="v1234_"))
    lora._DS_ROOT = tmp                      # noqa: SLF001
    ds_id = "t1"
    (tmp / ds_id / "images").mkdir(parents=True)
    items = []
    for iid, ang, *_ in REAL:
        (tmp / ds_id / "images" / f"{iid}.png").write_bytes(b"x")
        items.append({"id": iid, "angle": ang, "framing": FR[iid][0],
                      "status": "done"})
    # One row carries an existing QC block, to prove the pass edits rather than
    # replaces: v1.223's clobbering race is the reason this is checked.
    items[1]["qc"] = {"ok": True, "one_person": True, "identity_score": 0.61,
                      "angle_ok": True, "issues": ["keep me"]}
    (tmp / ds_id / "dataset.json").write_text(
        json.dumps({"id": ds_id, "items": items}), "utf-8")

    by_id = {r[0]: pv_of(r) for r in REAL}
    lk_pose_real, lk_avail_real = lk.pose, lk.available
    # The rig writes one-byte placeholder files, so rembg (if installed) prints
    # forty "cannot identify image file" warnings and every crop reads
    # unmeasured. Stub it: this block tests the ANGLE/FRAMING route, and the
    # crop rule has its own section against real measurements.
    lora._subj.box = lambda p: None                              # noqa: SLF001
    lora._subj.available = lambda: False                         # noqa: SLF001
    lora._like.pose = lambda p: by_id.get(Path(p).stem)          # noqa: SLF001
    lora._like.available = lambda: True                          # noqa: SLF001
    lora._like.angle_health = lambda: {                          # noqa: SLF001
        "available": True, "bands": {k: list(v) for k, v in lk.ANGLE_BANDS.items()},
        "sign": "negative yaw = left", "error": None}
    try:
        res = asyncio.run(lora.dataset_angles(ds_id))
    finally:
        lk.pose, lk.available = lk_pose_real, lk_avail_real

    check("every rendered image measured", res["measured"] == 40, str(res["measured"]))
    ba = res["by_angle"]
    check("back: 4 of 4 ok", ba["back"]["ok"] == 4, json.dumps(ba["back"]))
    check("profile_left: 4 of 4 ok", ba["profile_left"]["ok"] == 4)
    check("profile_right: 4 of 4 ok", ba["profile_right"]["ok"] == 4)
    check("front: 10 ok, 2 unmeasured",
          ba["front"]["ok"] == 10 and ba["front"]["unmeasured"] == 2, json.dumps(ba["front"]))
    check("three_quarter_left: 3 ok, 5 miss, 0 unmeasured",
          (ba["three_quarter_left"]["ok"], ba["three_quarter_left"]["miss"],
           ba["three_quarter_left"]["unmeasured"]) == (3, 5, 0),
          json.dumps(ba["three_quarter_left"]))
    check("three_quarter_right: 4 ok, 4 miss",
          (ba["three_quarter_right"]["ok"], ba["three_quarter_right"]["miss"]) == (4, 4),
          json.dumps(ba["three_quarter_right"]))
    check("median yaw reported per angle",
          ba["profile_right"]["yaw_median"] is not None,
          str(ba["profile_right"]["yaw_median"]))

    bf = res["by_framing"]
    check("the route reports by_framing too", set(bf) == {"face", "headshot", "upper", "full"},
          str(sorted(bf)))
    check("face: 7 ok, 1 wrong (0001 has no face)",
          (bf["face"]["ok"], bf["face"]["miss"]) == (7, 1), json.dumps(bf["face"]))
    check("upper: 8 ok, 1 wrong (0021), 3 unmeasured (back rows)",
          (bf["upper"]["ok"], bf["upper"]["miss"], bf["upper"]["unmeasured"]) == (8, 1, 3),
          json.dumps(bf["upper"]))
    check("full: 11 ok, 1 unmeasured (the back row)",
          (bf["full"]["ok"], bf["full"]["unmeasured"]) == (11, 1), json.dumps(bf["full"]))
    check("median face height reported per shot type",
          bf["full"]["face_h_median"] is not None, str(bf["full"]["face_h_median"]))
    check("the route returns the calibration it used",
          res["framing_cal"]["order_ok"] is True
          and set(res["framing_cal"]["medians"]) == {"face", "headshot", "upper", "full"},
          json.dumps(res["framing_cal"]["medians"]))
    saved_cal = json.loads((tmp / ds_id / "dataset.json").read_text("utf-8")).get("framing_cal")
    check("the calibration is STORED, so QC judges by the same numbers",
          bool(saved_cal) and saved_cal["medians"] == res["framing_cal"]["medians"])

    print("\n== what it wrote to disk ==")
    after = json.loads((tmp / ds_id / "dataset.json").read_text("utf-8"))
    q = {it["id"]: (it.get("qc") or {}) for it in after["items"]}
    check("yaw persisted", q["0009"]["yaw"] == 36.8, str(q["0009"].get("yaw")))
    check("angle_method says how it was decided",
          q["0009"]["angle_method"] == "head-yaw", str(q["0009"].get("angle_method")))
    check("an unmeasured row is not failed",
          q["0021"]["angle_ok"] is True and q["0021"]["angle_method"] == "unmeasured")
    check("v1.238 recovers 0007 as a measured miss",
          q["0007"]["angle_ok"] is False and q["0007"]["angle_method"] == "head-yaw")
    check("a measured miss is recorded as a miss", q["0035"]["angle_ok"] is False)
    check("existing QC fields survive the pass",
          q["0002"].get("identity_score") == 0.61 and q["0002"].get("issues") == ["keep me"],
          json.dumps(q["0002"]))
    # 0002 is yaw -19.3 on a three-quarter-left row: inside the old 18-degree
    # floor, outside the 20 my eyes support.  It arrived carrying angle_ok TRUE
    # and must leave carrying FALSE — that is the whole point of the pass.
    check("the pre-existing angle_ok was CORRECTED, not left alone",
          q["0002"]["angle_ok"] is False and q["0002"]["angle_method"] == "head-yaw",
          str(q["0002"].get("angle_ok")))
    check("a row with no prior QC gets one",
          isinstance(q["0035"], dict) and q["0035"].get("angle_note"))

    print("\n== flag summary ==")
    f = res["flags"]
    check("angle_off counts the measured misses", f["angle_off"] == 9, str(f["angle_off"]))
    check("angle_measured counts only measured rows",
          f["angle_measured"] == 38, str(f["angle_measured"]))
    check("angle_unmeasured is its own bucket — 0001 no face, 0021 bad fit",
          f["angle_unmeasured"] == 2, str(f["angle_unmeasured"]))
    check("framing_off counts the two broken images", f["framing_off"] == 2,
          str(f["framing_off"]))
    check("framing_measured is 36, framing_unmeasured is the 4 back rows",
          (f["framing_measured"], f["framing_unmeasured"]) == (36, 4),
          f"{f['framing_measured']}/{f['framing_unmeasured']}")
    # rembg is absent in this offline rig, so crop reports itself as unchecked —
    # which is the honest state, not a silent pass.
    # v1.261 added the wardrobe check, and this fixture has never run one, so
    # the honest answer is both. Still an exact-set assertion.
    check("crop AND wardrobe report UNCHECKED when neither has run",
          f["not_checked"] == ["crop", "wardrobe"], str(f["not_checked"]))
    check("nothing in this fixture was measured for wardrobe",
          (f["wardrobe_measured"], f["bare_skin"]) == (0, 0),
          f"{f['wardrobe_measured']}/{f['bare_skin']}")
    check("expression is the only thing still called unreliable",
          f["unreliable"] == ["expression"], str(f["unreliable"]))

    print("\n== v1.247: a profile is scored against a PROFILE ==")
    # Measured on dorian-v1: profile_left 0.42/0.33/0.40 and profile_right 0.33,
    # against nothing else in the dataset below 0.45. Geometry, not drift.
    SETS = {"front": (["F1", "F2"], ["front base", "face reference"]),
            "left": (["L1"], ["left reference"]),
            "right": (["R1"], ["right reference"])}
    for ang, want in (("front", "front"), ("three_quarter_left", "front"),
                      ("three_quarter_right", "front"), ("back", "front"),
                      ("profile_left", "left"), ("profile_right", "right")):
        embs, labels, key = lora._baselines_for(SETS, ang)       # noqa: SLF001
        check(f"{ang} -> the {want} baselines", key == want, f"{key} {labels}")
    check("a profile no longer rests on the frontal set",
          lora._baselines_for(SETS, "profile_right")[0] == ["R1"])   # noqa: SLF001
    check("three-quarter rows stay on the frontal set — they are mostly frontal "
          "and measured 0.50-0.76 there",
          lora._baselines_for(SETS, "three_quarter_left")[0] == ["F1", "F2"])  # noqa: SLF001

    # A character with no side references must degrade to frontal and SAY so,
    # or a geometry-penalised score reads like a clean one.
    THIN = {"front": (["F1"], ["front base"]), "left": ([], []), "right": ([], [])}
    embs, labels, key = lora._baselines_for(THIN, "profile_left")  # noqa: SLF001
    check("no left reference -> frontal fallback, named as a fallback",
          embs == ["F1"] and "no left reference" in key, key)
    check("an unknown angle falls back to frontal rather than to nothing",
          lora._baselines_for(SETS, "sideways-ish")[2] == "front")  # noqa: SLF001
    check("the old builder is now a shim onto the same frontal set",
          "_baseline_sets" in inspect.getsource(lora._likeness_baselines))  # noqa: SLF001

    print("\n== v1.246: the crop rule, against the 20 real redv1 measurements ==")
    from backend.services import subject as subj
    # id, framing, body_h, y1, y2, coverage — straight from crop_probe.
    CROP = [
        ("0001", "face", 1.000, 0.000, 0.999, 0.650),
        ("0002", "face", 1.000, 0.000, 0.999, 0.631),
        ("0003", "face", 1.000, 0.000, 0.999, 0.630),
        ("0004", "face", 1.000, 0.000, 0.999, 0.643),
        ("0005", "headshot", 0.971, 0.029, 0.999, 0.556),
        ("0006", "headshot", 1.000, 0.000, 0.999, 0.594),
        ("0007", "headshot", 0.994, 0.006, 0.999, 0.598),
        ("0008", "headshot", 0.993, 0.007, 0.999, 0.549),
        ("0009", "upper", 0.964, 0.036, 0.999, 0.389),
        ("0010", "upper", 0.888, 0.112, 0.999, 0.374),
        ("0011", "upper", 0.927, 0.073, 0.999, 0.442),
        ("0012", "upper", 0.810, 0.190, 0.999, 0.290),
        ("0013", "upper", 0.824, 0.176, 0.999, 0.307),
        ("0014", "upper", 0.931, 0.069, 0.999, 0.431),
        ("0015", "full", 0.880, 0.094, 0.974, 0.190),
        ("0016", "full", 0.948, 0.024, 0.971, 0.207),
        ("0017", "full", 0.844, 0.109, 0.952, 0.204),
        ("0018", "full", 0.836, 0.120, 0.955, 0.206),
        ("0019", "full", 0.871, 0.094, 0.964, 0.238),
        ("0020", "full", 0.807, 0.143, 0.949, 0.206),
    ]

    def _bx(h, y1, y2, cov):
        return {"coverage": cov, "dominance": 1.0, "x1": 0.1, "x2": 0.9,
                "y1": y1, "y2": y2, "body_h_ratio": h, "body_w_ratio": 0.8,
                "touches_top": y1 <= subj.EDGE_TOL,
                "touches_bottom": y2 >= 1 - subj.EDGE_TOL,
                "touches_left": False, "touches_right": False, "trustworthy": True}

    cv = {i: subj.crop_verdict(fr, _bx(h, y1, y2, c)) for i, fr, h, y1, y2, c in CROP}
    check("all 20 real images pass the corrected rule",
          all(v[0] is True for v in cv.values()),
          str([i for i, v in cv.items() if v[0] is not True]))
    # The seven the FIRST rule called wrong. A close-up filling the frame is the
    # shot, not a defect, and this is the regression that keeps it that way.
    check("the 4 face crops that v1.245 failed now pass",
          all(cv[i][0] is True for i in ("0001", "0002", "0003", "0004")))
    check("the 3 headshots that v1.245 failed now pass",
          all(cv[i][0] is True for i in ("0006", "0007", "0008")))
    check("the top edge is NOT checked on face or headshot",
          subj.CHECK_TOP["face"] is False and subj.CHECK_TOP["headshot"] is False)
    check("the top edge IS still checked on upper and full",
          subj.CHECK_TOP["upper"] is True and subj.CHECK_TOP["full"] is True)

    print("\n== and it still catches the three real failure modes ==")
    check("a full-body shot with the feet cut off fails",
          subj.crop_verdict("full", _bx(0.94, 0.06, 0.999, 0.2))[0] is False)
    check("an 'upper' floating clear of the bottom fails",
          subj.crop_verdict("upper", _bx(0.70, 0.10, 0.80, 0.3))[0] is False)
    check("an 'upper' with the head cut off fails",
          subj.crop_verdict("upper", _bx(0.99, 0.005, 0.999, 0.4))[0] is False)
    check("a mask that is not one subject is UNMEASURED, never failed",
          subj.crop_verdict("full", {"coverage": 0.95, "dominance": 0.4,
                                     "trustworthy": False})[0] is None)

    print("\n== the upper/full separation v1.243 could not make ==")
    ups = [(h, y2) for _i, fr, h, _y1, y2, _c in CROP if fr == "upper"]
    fus = [(h, y2) for _i, fr, h, _y1, y2, _c in CROP if fr == "full"]
    check("subject HEIGHT does not separate them (the v1.243 limit, confirmed)",
          min(h for h, _ in ups) < max(h for h, _ in fus)
          and min(h for h, _ in fus) < max(h for h, _ in ups),
          f"upper {min(h for h,_ in ups):.2f}-{max(h for h,_ in ups):.2f} vs "
          f"full {min(h for h,_ in fus):.2f}-{max(h for h,_ in fus):.2f}")
    check("the bottom EDGE separates them perfectly",
          all(y2 >= 1 - subj.EDGE_TOL for _h, y2 in ups)
          and all(y2 < 1 - subj.EDGE_TOL for _h, y2 in fus))

    print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
