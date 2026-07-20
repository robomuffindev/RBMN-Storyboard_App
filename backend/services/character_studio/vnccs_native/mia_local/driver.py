"""RBMN MIA rigging driver (v1.174) -- runs INSIDE the dedicated MIA venv.

Rigs a character mesh with Make-It-Animatable and writes a Mixamo-skeleton
FBX.  Called as a subprocess by mia_rig.py; never imported by the app.

    python driver.py --mesh character.glb --out rigged.fbx \
        --models-dir <dir with bw.pth etc.> [--device cpu|cuda|auto]
        [--use-normal] [--fingers] [--no-rest]

Progress protocol on stdout (parsed by mia_rig.py):
    MIA_PHASE <text>     -- coarse phase change
    MIA_STEP n/m         -- pipeline step counter (from the ProgressBar shim)
    MIA_DONE <fbx path>  -- success (last line)
Errors: nonzero exit code, traceback on stderr.
"""
import argparse
import logging
import os
import sys
import traceback
import warnings
from pathlib import Path

_HERE = Path(__file__).parent.absolute()

# v1.174.2: everything visible on stdout -- the vendored code logs each
# pipeline stage via logging; surfacing it as MIA_LOG lines lets the app show
# real progress and pinpoints any hang. Warnings are tamed (they'd otherwise
# flood the pipe).
logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="MIA_LOG %(message)s", force=True)
warnings.filterwarnings("ignore")


def _phase(text: str) -> None:
    print(f"MIA_PHASE {text}", flush=True)


def _load_mesh(path: str):
    import trimesh
    m = trimesh.load(path)
    if isinstance(m, trimesh.Scene):
        geoms = [g for g in m.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError(f"No triangle meshes inside {path}")
        m = geoms[0] if len(geoms) == 1 else trimesh.util.concatenate(geoms)
    if not isinstance(m, trimesh.Trimesh):
        raise ValueError(f"Unsupported mesh type {type(m).__name__} from {path}")
    if len(m.vertices) == 0 or len(m.faces) == 0:
        raise ValueError(f"Empty mesh from {path}")
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True, help="input mesh (.glb/.obj/.fbx)")
    ap.add_argument("--out", required=True, help="output rigged .fbx path")
    ap.add_argument("--models-dir", required=True, help="dir containing bw.pth etc.")
    ap.add_argument("--device", default="auto", help="auto | cpu | cuda")
    ap.add_argument("--dtype", default="fp32", help="fp32 | fp16 | bf16")
    ap.add_argument("--fingers", action="store_true",
                    help="keep finger bones (default merges them into the hand)")
    ap.add_argument("--use-normal", action="store_true",
                    help="use surface normals for skinning (helps close limbs)")
    ap.add_argument("--no-rest", action="store_true",
                    help="keep input pose instead of resetting to T-pose rest")
    args = ap.parse_args()

    # Environment for the shims/vendored code -- must be set BEFORE imports
    os.environ["MIA_MODELS_PATH"] = str(Path(args.models_dir).absolute())
    if args.device and args.device != "auto":
        os.environ["MIA_DEVICE"] = args.device
    # Our comfy/folder_paths shims + the pack live here
    sys.path.insert(0, str(_HERE))

    _phase("loading libraries")
    import torch  # noqa: F401  (also loads torch_cluster's torch dep first)
    from pack import mia_inference

    dev = os.environ.get("MIA_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"MIA_PHASE device {dev}", flush=True)
    if dev == "cpu":
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))

    _phase("loading mesh")
    mesh = _load_mesh(args.mesh)
    print(f"MIA_PHASE mesh {len(mesh.vertices)}v/{len(mesh.faces)}f", flush=True)

    _phase("loading models")
    cache_key = mia_inference.load_mia_models(dtype=args.dtype)
    models = mia_inference.get_cached_models(cache_key)

    _phase("rigging")
    out_path = str(Path(args.out).absolute())
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    result = mia_inference.run_mia_inference(
        mesh=mesh,
        models=models,
        output_path=out_path,
        no_fingers=not args.fingers,
        use_normal=args.use_normal,
        reset_to_rest=not args.no_rest,
    )
    if not result or not os.path.exists(result):
        print("driver: run_mia_inference returned no output file", file=sys.stderr, flush=True)
        return 3
    print(f"MIA_DONE {result}", flush=True)
    # v1.174.5: bpy-as-module registers atexit/teardown handlers that can
    # crash AFTER all work is done (nonzero exit despite success). The FBX is
    # on disk and stdout is flushed -- skip interpreter finalization entirely.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
