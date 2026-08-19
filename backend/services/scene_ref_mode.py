"""🎛 SCENE REF MODE — how a scene carries character identity (v1.277.37).

His framing, verbatim: *"there are 2 routes the autogen can take. 1. is if we
use t2i and swap the references in. 2. is if we use full reference mode in
minimax."* Both already existed in the codebase; neither was ever a CHOICE.

    t2i_swap        Pass 1 renders the scene with NO refs (klein_t2i), Pass 2
                    composites the characters in (klein_Nref). The frame is
                    then the whole plan — video runs plain i2v from it.
                    ⭐ Strongest COMPOSITION: the model stages the shot before
                    anyone is in it, so blocking/camera/lighting are not fought
                    over by four reference images.

    full_reference  The characters' sheets go to the model AS references and it
                    carries identity natively — single-pass image, and on
                    MiniMax H3 the video routes to `h3_ref2v`.
                    ⭐ Strongest IDENTITY, and the only route that keeps a face
                    consistent THROUGH the motion rather than only in frame 1.

    inherit         (per scene, the default) ask the project.

**Why this had to become explicit.** Before it, `h3_i2v` silently became
`h3_ref2v` whenever refs merely EXISTED, and two-pass was a checkbox nobody set
per scene. So the two routes could never be compared on the same scene, and the
auto-gen chain had no way to be told which one to run — it would just inherit
whatever the refs happened to be that day. A default that emerges from data is
not a default; it is a coin flip with extra steps.

**Precedence: scene > project > DEFAULT.** `inherit`/`project`/`""` on a scene
all mean "ask the project" — the UI writes `inherit`, older rows have nothing,
and both must behave the same.

⚠ This module is deliberately pure and dependency-free: the dispatcher imports
it from inside a job, and `generation.py` from inside a request. Anything that
touches the session here would drag a transaction into both.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

#: the two real routes
MODES = ("t2i_swap", "full_reference")

#: ⚠ full_reference is the default because it is what the code ALREADY did
#: (refs present ⇒ ref2v). Changing the default here silently re-routes every
#: existing project that never set the field.
DEFAULT = "full_reference"

#: what a scene may store to mean "ask the project"
_INHERIT = ("", "inherit", "project", "default", "none")

LABELS = {
    "t2i_swap": "T2I → swap refs in (two-pass)",
    "full_reference": "Full reference mode",
    "inherit": "Use the project default",
}


def normalise(value: Any) -> Optional[str]:
    """A stored value → a known mode, or None for 'inherit / not set'."""
    v = str(value or "").strip().lower()
    if v in _INHERIT:
        return None
    if v in MODES:
        return v
    # tolerate the shapes the UI and older notes used
    if v in ("two_pass", "twopass", "t2i", "swap", "t2i_then_swap"):
        return "t2i_swap"
    if v in ("ref", "refs", "reference", "ref2v", "full", "fullref"):
        return "full_reference"
    return None


def project_mode(settings: Optional[Mapping[str, Any]]) -> str:
    """The project-wide default set on the Concept tab."""
    return normalise((settings or {}).get("scene_ref_mode")) or DEFAULT


def scene_override(scene_params: Optional[Mapping[str, Any]]) -> Optional[str]:
    """What THIS scene asked for, or None if it defers to the project.

    ⚠ `two_pass_enabled` is the legacy spelling of route 1 — it is the checkbox
    that shipped long before the modes had names, and scenes in every existing
    project still carry it. Reading it here is what stops the new dropdown from
    silently re-routing work the user already set up by hand."""
    sp = scene_params or {}
    explicit = normalise(sp.get("scene_ref_mode"))
    if explicit:
        return explicit
    if sp.get("two_pass_enabled"):
        return "t2i_swap"
    return None


def resolve(settings: Optional[Mapping[str, Any]],
            scene_params: Optional[Mapping[str, Any]] = None) -> str:
    """The mode that actually applies to ONE scene. Never returns 'inherit'."""
    return scene_override(scene_params) or project_mode(settings)


def wants_two_pass(mode: str) -> bool:
    """t2i_swap IS two-pass — that is the whole of route 1."""
    return normalise(mode) == "t2i_swap"


def explain(settings: Optional[Mapping[str, Any]],
            scene_params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """For the UI and the logs: what applies, and WHO decided it.

    A resolved value alone is unreadable in a log — 'full_reference' does not
    say whether the scene asked for it or merely failed to override."""
    scene_raw = scene_override(scene_params)
    proj = project_mode(settings)
    return {
        "mode": scene_raw or proj,
        "project_mode": proj,
        "scene_override": scene_raw,
        "source": "scene" if scene_raw else "project",
        "label": LABELS.get(scene_raw or proj, scene_raw or proj),
    }
