"""Set up local 3D character rigging (Make-It-Animatable) -- v1.174.1.

Called by install.bat (and safe to run any time from the app venv):

    python tools/setup_mia.py            # full setup: env + weights (~2.2GB)
    python tools/setup_mia.py --env-only # just the python env (no big download)
    python tools/setup_mia.py --status   # show what's ready

Everything is idempotent: existing env/weights are detected and skipped, and
a dependency-set change (env tag bump in mia_rig.py) triggers a clean rebuild
on the next run -- which is what makes re-running install.bat the updater.
If this is never run, the app still bootstraps MIA automatically on the first
3D-body rig; this script just front-loads the wait.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.character_studio.vnccs_native import mia_rig  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-only", action="store_true",
                    help="create the MIA python env but skip the ~2.2GB weight download")
    ap.add_argument("--status", action="store_true", help="report readiness and exit")
    args = ap.parse_args()

    def say(msg: str) -> None:
        print(f"  [mia] {msg}", flush=True)

    if args.status:
        print(f"  MIA env:     {'READY' if mia_rig.env_ready() else 'not set up'}  ({mia_rig.VENV_DIR})")
        print(f"  MIA weights: {'READY' if mia_rig.weights_ready() else 'not downloaded'}  ({mia_rig.MODELS_DIR})")
        print(f"  GPU (NVIDIA): {'yes' if mia_rig.has_cuda() else 'no -- CPU rigging (~1-3 min/character)'}")
        return 0

    try:
        say("checking python environment...")
        mia_rig.ensure_venv(say)
        if not args.env_only:
            say("checking model weights...")
            mia_rig.ensure_weights(say)
        say("done. " + ("Weights will download on first rig." if args.env_only
                        else "Local 3D rigging is fully ready."))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"  [mia][ERROR] {e}", file=sys.stderr, flush=True)
        print("  [mia] Not fatal: the app will retry this automatically on the "
              "first 3D-body rig.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
