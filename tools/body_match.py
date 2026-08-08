#!/usr/bin/env python3
"""BODY MATCH (v1.199.124) -- does the generated base have the REFERENCE's build?

Turns "the body looks off" into numbers.  Extracts each figure's silhouette and
reports WIDTH / FIGURE-HEIGHT at fixed height fractions -- a scale-free shape
signature that does not care about canvas size, crop or zoom.  Compare the
reference photo, the generated base view, and (optionally) the 3D scan itself to
see WHERE in the pipeline the build is lost.

  body_match.bat --char Duke                       (resolves everything itself)
  body_match.bat <reference.png> <generated.png> [more.png ...]
  body_match.bat ref.png gen.png --glb Duke        (also measure the scan)
  body_match.bat ref.png gen.png --glb path\\to\\character.glb

Reading it: rows 0.62-0.80 (hips/upper legs) are the honest comparison -- no arms
in the silhouette in ANY pose.  Rows 0.14-0.28 are the arms: meaningless across a
T-pose and an arms-down photo.  Rows 0.35-0.60 (chest/belly) are comparable only
between two figures posed the same way; an arms-down photo adds roughly 0.10-0.14
of width there because both arms sit against the torso.
Writes _diag/body_match/<stamp>/ (profile PNG + numbers.txt).
"""
from __future__ import annotations
import json, os, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROWS = [0.06, 0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38, 0.42, 0.46,
        0.50, 0.54, 0.58, 0.62, 0.66, 0.70, 0.74, 0.78, 0.86, 0.94]
CLEAN = [0.62, 0.66, 0.70, 0.74, 0.78]          # no arms in any pose


def _data_dir() -> Path:
    home = Path(os.path.expanduser(os.environ.get("PROJECT_DIR") or "~/RBMN-Projects"))
    for db in (home / "RBMN.db", Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db"):
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                return Path(os.path.expanduser(str(row[0])))
        except Exception:  # noqa: BLE001
            pass
    return home


def _safe(name: str) -> str:
    """Same slug ingest.save_base_preview uses for the on-disk base folder."""
    return "".join(ch if (ch.isalnum() or ch in " _-") else "_"
                   for ch in (name or "char")).strip() or "char"


def _find_base_front(character: str):
    """Newest generated FRONT base view for this character.

    Layout (ingest.save_base_preview):
      <project_dir>/<project_id>/assets/vnccs/<safe name>/base/base_<ver>_front.png
    The project id is a uuid we do not need to know -- glob it.
    """
    root = _data_dir()
    if not root.is_dir():
        return None
    hits = sorted(root.glob(f"*/assets/vnccs/{_safe(character)}/base/base_*_front.png"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _axial_frac(path: Path) -> float:
    """How AXIAL (front- or back-facing) a silhouette is: the fraction of rows in
    the legs band that show a gap BETWEEN TWO LEGS.

    v1.199.133.  A turnaround caches a front, both sides and a back photo, all
    1024x1536 portraits of the same subject, all re-cached within the same
    second -- so "newest portrait" picked an arbitrary one, and on 2026-07-29
    01:12 it picked the PROFILE.  A width/height signature measured against a
    profile is meaningless (it read 105% where the front read 72%).  Two legs
    with air between them exist in a front or back view and in neither profile.
    """
    import numpy as np
    try:
        m, _how = mask_from_image(path)
    except Exception:  # noqa: BLE001
        return -1.0
    ys = np.where(m.sum(1) > 3)[0]
    xs = np.where(m.sum(0) > 3)[0]
    if not len(ys) or not len(xs):
        return -1.0
    sub = m[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
    H = sub.shape[0]
    band = range(int(H * 0.72), int(H * 0.95))
    n = 0
    for i in band:
        idx = np.where(sub[i])[0]
        if len(idx) < 2:
            continue
        if (np.diff(idx) > max(3, H * 0.012)).any():
            n += 1
    return n / max(len(list(band)), 1)


def _find_ref_photo(character: str):
    """Best full-body AXIAL reference PHOTO from the Klein ref disk cache.

    Cache files are sha256(name)[:24].png, so the original filename is gone --
    pick by shape instead: a large PORTRAIT image (aspect >= 1.2), which is what
    a head-to-toe body reference always is (face crops are small or landscape),
    and then require it to be FRONT- or BACK-facing via _axial_frac.  Everything
    is printed so a wrong pick is obvious and overridable (pass paths instead).
    """
    d = REPO / "runtime" / "klein_ref_cache"
    if not d.is_dir():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    cands = []
    for f in d.glob("*.png"):
        try:
            w, h = Image.open(f).size
        except Exception:  # noqa: BLE001
            continue
        if h < 1.2 * w or w * h < 1_400_000:
            continue
        st = f.stat()
        # RECENCY first: the refs of the character you are working on right now
        # were re-cached on this session's runs, while older characters' refs sit
        # at their original dates.  Area then byte-size break exact ties (two
        # 1024x1536 refs of the same subject -- take the more detailed one).
        cands.append(((int(st.st_mtime), w * h, st.st_size), f))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)
    for _key, f in cands[:6]:
        g = _axial_frac(f)
        if g >= 0.10:
            print(f"  ref view test: {f.name} legs-apart rows {g:.2f} -> AXIAL (front/back)")
            return f
        print(f"  ref view test: {f.name} legs-apart rows {g:.2f} -> skipped (profile/unclear)")
    print("  WARNING: no axial reference found; falling back to the newest portrait")
    return cands[0][1]


def _find_glb(character: str):
    root = _data_dir() / "mesh3d"
    if not root.is_dir():
        return None
    named, unnamed = [], []
    for d in sorted(root.iterdir()):
        glb = d / "character.glb"
        if not glb.exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        nm = str(meta.get("character_name") or "").strip()
        (named if nm.lower() == character.lower() else (unnamed if not nm else [])).append(glb)
    if named:
        return named[0]
    return unnamed[0] if len(unnamed) == 1 else None


def _largest(m):
    from scipy import ndimage
    lab, n = ndimage.label(m)
    if not n:
        return m
    import numpy as np
    return lab == (int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1)


def _clean(m):
    import numpy as np
    from scipy import ndimage
    return ndimage.binary_fill_holes(ndimage.binary_closing(_largest(m), np.ones((9, 9))))


def mask_from_image(path: Path):
    """Alpha when present, else GrabCut -- studio backdrops have gradients and a
    cast shadow, which a flat colour-distance threshold happily swallows."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    a = np.asarray(Image.open(path).convert("RGBA"))
    if a[:, :, 3].min() < 250:
        return _clean(a[:, :, 3] > 128), "alpha"
    import cv2
    bgr = cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    mk = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    mk[:int(h * .02), :] = cv2.GC_BGD;  mk[int(h * .99):, :] = cv2.GC_BGD
    mk[:, :int(w * .04)] = cv2.GC_BGD;  mk[:, int(w * .96):] = cv2.GC_BGD
    mk[int(h * .30):int(h * .80), int(w * .40):int(w * .60)] = cv2.GC_FGD
    cv2.grabCut(bgr, mk, None, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64),
                10, cv2.GC_INIT_WITH_MASK)
    m = (mk == cv2.GC_FGD) | (mk == cv2.GC_PR_FGD)
    core = _largest(ndimage.binary_erosion(m, np.ones((21, 21))))    # cut shadow bridges
    return _clean(_largest(m & ndimage.binary_dilation(core, np.ones((45, 45))))), "grabcut"


def mask_from_glb(path: Path, res: int = 1024):
    """Front-orthographic silhouette of the scan -- same signature, same units."""
    import numpy as np
    try:
        import trimesh
    except ImportError:
        raise SystemExit("ERROR: --glb needs trimesh.  Install it into the app venv:\n"
                         "   venv\\Scripts\\python.exe -m pip install trimesh\n"
                         "(the MIA runtime venv already has it: runtime\\mia\\venv)")
    # force="mesh" concatenates every node of the glTF scene into ONE Trimesh --
    # a GLB straight out of Hunyuan can carry several primitives.
    mesh = trimesh.load(str(path), force="mesh")
    if not hasattr(mesh, "vertices"):
        mesh = trimesh.util.concatenate(
            [g for g in getattr(mesh, "geometry", {}).values() if hasattr(g, "vertices")])
    if not len(getattr(mesh, "vertices", ())):
        raise SystemExit(f"ERROR: {path} has no vertices")
    # Splat the SURFACE, not the vertex list: a silhouette rasterised from
    # vertices alone is hollow wherever the mesh has large flat triangles, and
    # the width profile then reads zero mid-body.  Dense area-weighted surface
    # samples fill it regardless of tessellation.
    try:
        v = np.asarray(mesh.sample(400_000), float)
    except Exception:  # noqa: BLE001
        v = np.asarray(mesh.vertices, float)
    x, y = v[:, 0], v[:, 1]                       # glTF: +Y up, -Z forward
    # np.ptp(): numpy 2 removed the ndarray.ptp() METHOD.
    sx = (res - 8) / max(float(np.ptp(x)), 1e-9)
    sy = (res * 2 - 8) / max(float(np.ptp(y)), 1e-9)
    s = min(sx, sy)
    px = ((x - x.min()) * s + 4).astype(int)
    py = ((y.max() - y) * s + 4).astype(int)      # flip: image rows go down
    m = np.zeros((py.max() + 5, px.max() + 5), bool)
    m[py, px] = True
    from scipy import ndimage                      # close the point cloud into a solid
    m = ndimage.binary_closing(m, np.ones((9, 9)))
    return ndimage.binary_fill_holes(_largest(m)), "glb"


def profile(m, name, how):
    import numpy as np
    ys = np.where(m.sum(1) > 3)[0]
    if not len(ys):
        raise SystemExit(f"ERROR: {name}: empty silhouette")
    y0, y1 = int(ys.min()), int(ys.max())
    H = y1 - y0 + 1
    w = np.zeros(H)
    for i, y in enumerate(range(y0, y1 + 1)):
        xs = np.where(m[y])[0]
        if len(xs):
            w[i] = xs.max() - xs.min() + 1
    return dict(name=name, how=how, H=H, w=w / H, m=m)


def main() -> int:
    args = sys.argv[1:]
    glb_arg = None
    if "--glb" in args:
        i = args.index("--glb")
        glb_arg = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    char = None
    if "--char" in args:
        i = args.index("--char")
        char = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    imgs = [Path(a) for a in args if not a.startswith("-")]

    # --char resolves BOTH ends from disk so nothing has to be typed by hand:
    # the reference photo from the Klein ref cache, the newest generated FRONT
    # base view from the project store.  Explicit paths always win.
    if char:
        if not imgs:
            ref = _find_ref_photo(char)
            if ref:
                print(f"resolved reference photo : {ref}")
                imgs.append(ref)
            else:
                print(f"WARNING: no reference photo found under "
                      f"{REPO / 'runtime' / 'klein_ref_cache'}")
        gen = _find_base_front(char)
        if gen:
            print(f"resolved generated front : {gen}")
            imgs.append(gen)
        else:
            print(f"WARNING: no base_*_front.png for {char!r} under "
                  f"{_data_dir()}/*/assets/vnccs/{_safe(char)}/base/")
        if glb_arg is None:
            glb_arg = char
        print()

    if not imgs and not glb_arg:
        print(__doc__)
        return 1

    ps = []
    for p in imgs:
        if not p.exists():
            print(f"ERROR: no such file: {p}")
            return 2
        m, how = mask_from_image(p)
        ps.append(profile(m, p.name, how))
    if glb_arg:
        g = Path(glb_arg)
        if not g.exists():
            g = _find_glb(glb_arg) or g
        if not g.exists():
            print(f"WARNING: no character.glb for {glb_arg!r} under {_data_dir() / 'mesh3d'} "
                  f"-- skipping the scan")
        else:
            m, how = mask_from_glb(g)
            ps.append(profile(m, f"SCAN {g.parent.name}", how))

    out = []
    out.append("BODY MATCH -- width / figure-height (scale-free shape signature)\n")
    for p in ps:
        out.append(f"  {p['name']}   height {p['H']}px   segmented via {p['how']}")
    out.append("")
    out.append(f"{'y frac':>7}" + "".join(f"{p['name'][:16]:>18}" for p in ps)
               + ("".join(f"{'ratio->'+p['name'][:8]:>18}" for p in ps[1:]) if len(ps) > 1 else ""))
    for f in ROWS:
        v = [p["w"][int(round(f * (p["H"] - 1)))] for p in ps]
        tag = "  <- arms" if 0.13 <= f <= 0.29 else ("  <- clean" if f in CLEAN else "")
        out.append(f"{f:>7.2f}" + "".join(f"{x:>18.3f}" for x in v)
                   + "".join(f"{(x / v[0] if v[0] else 0):>18.2f}" for x in v[1:]) + tag)
    if len(ps) > 1:
        out.append("")
        for p in ps[1:]:
            import numpy as np
            r = [p["w"][int(round(f * (p["H"] - 1)))] / max(ps[0]["w"][int(round(f * (ps[0]["H"] - 1)))], 1e-9)
                 for f in CLEAN]
            out.append(f"  {p['name'][:28]:<30} hips/legs vs reference: "
                       f"{100 * float(np.mean(r)):.0f}%  (100% = same build)")
    txt = "\n".join(out)
    print(txt)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    d = REPO / "_diag" / "body_match" / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "numbers.txt").write_text(txt, encoding="utf-8")
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, len(ps) + 1, figsize=(5 * (len(ps) + 1), 8))
        yy = np.linspace(0, 1, 800)
        for i, p in enumerate(ps):
            ax[i].imshow(p["m"], cmap="gray"); ax[i].set_title(p["name"][:28]); ax[i].axis("off")
            ax[-1].plot(np.interp(yy, np.linspace(0, 1, p["H"]), p["w"]), yy, label=p["name"][:20])
        ax[-1].invert_yaxis(); ax[-1].grid(alpha=.3); ax[-1].legend(fontsize=8)
        ax[-1].set_xlabel("width / height"); ax[-1].set_ylabel("y fraction")
        plt.tight_layout(); plt.savefig(d / "profile.png", dpi=70); plt.close()
    except Exception as e:  # noqa: BLE001
        print(f"(profile image skipped: {e})")
    print(f"\nwrote {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
