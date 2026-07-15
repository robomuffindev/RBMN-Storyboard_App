"""Assemble submittable VNCCS Step graphs from our form values.

Loads a vendored VNCCS UI step graph (``workflows/vnccs/*.json``), converts it
to API format with the worker's ``object_info`` (see ``graph.py``), injects our
character form / generation settings / Control-Center config into the relevant
JSON widgets, and taps the generator's image outputs with SaveImage so results
return through ``/history``.

Each ``build_*`` returns ``(api_graph, tap_map)`` where ``tap_map`` is
``{output_label: save_node_id}`` — the ingest step downloads each label and
files it into our asset store + Studio catalog.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .graph import (
    ui_to_api_prompt,
    find_nodes_by_class,
    patch_json_widget,
    add_save_image_taps,
)

logger = logging.getLogger(__name__)

# Vendored step graphs live alongside the other workflow JSONs.
_WF_DIR = Path(__file__).resolve().parents[4] / "workflows" / "vnccs"

STEP_FILES = {
    "creator": "STEP1_CREATOR.json",
    "cloner": "STEP1_CLONER.json",
    "clothes": "STEP2_CLOTHES.json",
    "emotions": "STEP3_EMOTIONS.json",
}

# generator class + ordered (output_slot, label) taps per step
STEP_TAPS: Dict[str, Tuple[str, List[Tuple[int, str]]]] = {
    "creator": ("VNCCS_CharacterGenerator",
                [(0, "sheet"), (1, "faces"), (2, "pose_generation"), (3, "upscaled")]),
    "cloner":  ("VNCCS_CharacterCloneGenerator",
                [(0, "original_sprites"), (1, "faces"), (2, "naked_sprites"),
                 (3, "original_pose_generation"), (4, "original_upscaled"),
                 (5, "remove_clothes"), (6, "naked_pose_generation")]),
    "clothes": ("VNCCS_ClothesGenerator",
                [(0, "sprites"), (1, "faces"), (2, "source_upscaled"),
                 (3, "pose_generation"), (4, "upscaled")]),
    "emotions": ("VNCCS_EmotionsGenerator",
                 [(0, "sprites"), (1, "faces")]),
}

# Character-creator/cloner nodes whose widget_data.character_info we drive.
_CHAR_FORM_CLASSES = ("CharacterCreatorV2", "CharacterCloner")

# VNCCS character_info keys we own (everything else in the template is left as-is).
_CHAR_INFO_KEYS = (
    "sex", "age", "race", "skin_color", "hair", "eyes", "face", "body",
    "additional_details", "nsfw", "aesthetics", "negative_prompt",
    "lora_prompt", "background_color", "name", "seed",
)


# GUI meganodes that declare ``widget_data`` in the HIDDEN input section.  The
# UI->API converter only walks required+optional inputs, so their widget_data —
# which carries the WORKING baseline config (gen_settings with the selected
# base model, upscaler/bg-remove config, …) — is dropped unless we re-seed it
# from the vendored UI graph before patching.  Missing this was the cause of
# the live "No Checkpoint selected in Character Creator V2" failure: the
# creator ran with gen_settings={} -> illustrious mode -> empty ckpt_name.
_HIDDEN_WIDGET_DATA_CLASSES = ("CharacterCreatorV2", "CharacterCloner", "ClothesDesigner")
_WIDGET_DATA_MARKER_KEYS = ("character_info", "costume_info", "gen_settings")


def _seed_hidden_widget_data(ui: dict, api: Dict[str, dict]) -> None:
    """Copy each GUI meganode's original widget_data JSON blob from the vendored
    UI graph into the API graph, so ``patch_json_widget`` mutates the full
    working baseline instead of rebuilding from ``{}``."""
    for node in ui.get("nodes", []) or []:
        if node.get("type") not in _HIDDEN_WIDGET_DATA_CLASSES:
            continue
        nid = str(node.get("id"))
        entry = api.get(nid)
        if not entry or entry["inputs"].get("widget_data"):
            continue
        for wv in node.get("widgets_values") or []:
            if not (isinstance(wv, str) and wv.strip().startswith("{")):
                continue
            try:
                obj = json.loads(wv)
            except Exception:
                continue
            if isinstance(obj, dict) and any(k in obj for k in _WIDGET_DATA_MARKER_KEYS):
                entry["inputs"]["widget_data"] = wv
                break


def _merge_gen_settings(gs: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> None:
    """Merge our gen-settings overrides into a VNCCS gen_settings blob IN PLACE.

    VNCCS ``normalize_gen_settings`` applies ``mode_settings[generation_mode]``
    LAST, so a per-mode profile SHADOWS top-level keys.  Every override must
    therefore be written both top-level and into the active mode profile, or a
    baseline profile value (e.g. steps=12) silently wins over ours.
    """
    overrides = dict(overrides or {})
    mode = str(overrides.get("generation_mode") or gs.get("generation_mode") or "illustrious").lower()
    gs["generation_mode"] = mode
    overrides.pop("generation_mode", None)
    profile = None
    mode_settings = gs.get("mode_settings")
    if isinstance(mode_settings, dict):
        profile = mode_settings.setdefault(mode, {})
        if not isinstance(profile, dict):
            profile = None
    for k, v in overrides.items():
        gs[k] = v
        if profile is not None and k != "mode_settings":
            profile[k] = v
    # Fresh seed per run unless the caller pinned one: VNCCS's generate_seed(0)
    # rolls a random 64-bit seed, while the vendored baseline carries a FIXED
    # seed that would reproduce the template author's character every time.
    if "seed" not in overrides:
        gs["seed"] = 0
        if profile is not None:
            profile["seed"] = 0


def load_step_ui(step: str) -> dict:
    fname = STEP_FILES[step]
    with open(_WF_DIR / fname, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Widget mutators
# --------------------------------------------------------------------------- #
def map_character_info(ours: Dict[str, Any], *, name: str,
                       nsfw: bool = False, background: str = "Green") -> Dict[str, Any]:
    """Map our (already VNCCS-shaped) character_info onto the VNCCS keys.

    Our StudioCharacter.character_info follows the VNCCS tag-sheet schema, so this
    is mostly pass-through with defaults + the per-render name/nsfw/background.
    """
    out: Dict[str, Any] = {}
    for k in _CHAR_INFO_KEYS:
        if k in ours and ours[k] is not None:
            out[k] = ours[k]
    out["name"] = name
    out["nsfw"] = bool(nsfw)
    out["background_color"] = background or out.get("background_color", "Green")
    out.setdefault("sex", "female")
    out.setdefault("age", 18)
    out.setdefault("aesthetics", "masterpiece, best quality")
    return out


def _apply_character_form(api: Dict[str, dict], char_info: Dict[str, Any],
                          gen_settings: Optional[Dict[str, Any]]) -> None:
    """Patch the CharacterCreatorV2 / CharacterCloner widget_data in place."""
    for cls in _CHAR_FORM_CLASSES:
        for nid in find_nodes_by_class(api, cls):
            def mut(obj, _ci=char_info, _gs=gen_settings):
                ci = obj.setdefault("character_info", {})
                ci.update(_ci)
                obj["character"] = _ci.get("name", obj.get("character", ""))
                gs = obj.setdefault("gen_settings", {})
                _merge_gen_settings(gs, _gs)
            patch_json_widget(api, nid, "widget_data", mut)


def _apply_control_center(api: Dict[str, dict], cc: Optional[Dict[str, Any]]) -> None:
    """Patch VNCCS_ControlCenter node_state (selected model / loras / model_params)."""
    if not cc:
        return
    for nid in find_nodes_by_class(api, "VNCCS_ControlCenter"):
        def mut(obj, _cc=cc):
            if "selected_model" in _cc:
                obj["selected_model"] = _cc["selected_model"]
                obj.setdefault("selected_models", {})
                st = _cc.get("selected_type") or obj.get("selected_type")
                if st:
                    obj["selected_type"] = st
                    obj["selected_models"][st] = _cc["selected_model"]
            if "loras" in _cc:
                obj["loras"] = _cc["loras"]
            if "model_params" in _cc:
                obj.setdefault("model_params", {}).update(_cc["model_params"])
        patch_json_widget(api, nid, "node_state", mut)


def _apply_generator_settings(api: Dict[str, dict], step: str, *,
                              character_name: str, nsfw: bool,
                              generator_overrides: Optional[Dict[str, Any]]) -> None:
    """Patch the generator node's widget_data (character_name, nsfw_enabled, overrides)."""
    gen_cls = STEP_TAPS[step][0]
    for nid in find_nodes_by_class(api, gen_cls):
        def mut(obj, _n=character_name, _nsfw=nsfw, _ov=generator_overrides):
            obj["character_name"] = _n
            obj["nsfw_enabled"] = bool(_nsfw)
            if _ov:
                for k, v in _ov.items():
                    if isinstance(v, dict) and isinstance(obj.get(k), dict):
                        obj[k].update(v)
                    else:
                        obj[k] = v
        patch_json_widget(api, nid, "widget_data", mut)


def _apply_clothes_form(api: Dict[str, dict], *, character_name: str, costume_name: str,
                       costume_info: Dict[str, Any], clone_image: Optional[Dict[str, Any]],
                       clone_sam_prompt: Optional[str], background: str,
                       gen_settings: Optional[Dict[str, Any]]) -> None:
    """Patch the ClothesDesigner widget_data (Step 2)."""
    for nid in find_nodes_by_class(api, "ClothesDesigner"):
        def mut(obj, _n=character_name, _c=costume_name, _ci=costume_info,
                _img=clone_image, _sam=clone_sam_prompt, _bg=background, _gs=gen_settings):
            obj["character"] = _n
            obj["costume"] = _c or obj.get("costume") or "Costume"
            ci = obj.setdefault("costume_info", {})
            for slot in ("top", "bottom", "head", "face", "shoes"):
                if _ci.get(slot) is not None:
                    ci[slot] = _ci[slot]
            if _img is not None:
                obj["clone_image"] = _img          # {"name","type":"input","subfolder"} or None to clear
                obj["activeTab"] = "clone"
            if _sam is not None:
                obj["clone_sam_prompt"] = _sam
            gs = obj.setdefault("gen_settings", {})
            gs.setdefault("lora_name", "qwen/VNCCS/VNCCS_QIE2511_ClothesCore-RC3.7.safetensors")
            gs["background_color"] = _bg or gs.get("background_color", "Green")
            if _gs:
                gs.update({k: v for k, v in _gs.items() if k not in ("mode_settings",)})
        patch_json_widget(api, nid, "widget_data", mut)


def _apply_emotions_form(api: Dict[str, dict], *, character_name: str,
                         costumes: List[str], emotions: List[str],
                         generation_model: str = "Anima", prompt_style: str = "Anima",
                         gen_settings: Optional[Dict[str, Any]] = None) -> None:
    """Patch EmotionGeneratorV2 named inputs (Step 3).

    character/costumes_data/emotions_data are STRING widgets; costumes_data and
    emotions_data are JSON-encoded arrays (e.g. '["Original"]').  The node's
    ``generation_settings`` is a declared JSON-string widget carried over from
    the vendored graph — merge our saved overrides into it (mode-aware).
    """
    for nid in find_nodes_by_class(api, "EmotionGeneratorV2"):
        node_inputs = api[nid]["inputs"]
        node_inputs["character"] = character_name
        node_inputs["costumes_data"] = json.dumps(costumes or ["Original"])
        node_inputs["emotions_data"] = json.dumps(emotions or [])
        if "generation_model" in node_inputs or generation_model:
            node_inputs["generation_model"] = generation_model
        if "prompt_style" in node_inputs or prompt_style:
            node_inputs["prompt_style"] = prompt_style
        if gen_settings:
            raw_gs = node_inputs.get("generation_settings")
            try:
                gs = json.loads(raw_gs) if isinstance(raw_gs, str) and raw_gs.strip() else {}
            except Exception:
                gs = {}
            _merge_gen_settings(gs, gen_settings)
            node_inputs["generation_settings"] = json.dumps(gs)
            mode = gs.get("generation_mode")
            if mode in ("anima", "illustrious"):
                node_inputs["generation_model"] = "Anima" if mode == "anima" else "Illustrious"


_BASELINE_CACHE: Dict[str, Any] = {}


def _creator_baseline_widget_data() -> Dict[str, Any]:
    """Parse (and cache) the CharacterCreatorV2 widget_data baseline from the
    vendored STEP1_CREATOR graph — the WORKING gen_settings + template info."""
    hit = _BASELINE_CACHE.get("creator_wd")
    if hit is None:
        hit = {}
        ui = load_step_ui("creator")
        for node in ui.get("nodes", []) or []:
            if node.get("type") != "CharacterCreatorV2":
                continue
            for wv in node.get("widgets_values") or []:
                if isinstance(wv, str) and wv.strip().startswith("{"):
                    try:
                        obj = json.loads(wv)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and "gen_settings" in obj:
                        hit = obj
                        break
        _BASELINE_CACHE["creator_wd"] = hit
    return json.loads(json.dumps(hit))  # deep copy


def creator_baseline_gen_settings() -> Dict[str, Any]:
    """The vendored creator graph's working gen_settings (deep copy)."""
    return _creator_baseline_widget_data().get("gen_settings") or {}


def creator_baseline_pose_data() -> Dict[str, Any]:
    """The vendored creator graph's VNCCS_PoseStudio pose_data blob (deep copy):
    mesh params, export config, lights and the 12 default poses."""
    hit = _BASELINE_CACHE.get("creator_pd")
    if hit is None:
        hit = {}
        ui = load_step_ui("creator")
        for node in ui.get("nodes", []) or []:
            if node.get("type") != "VNCCS_PoseStudio":
                continue
            for wv in node.get("widgets_values") or []:
                if isinstance(wv, str) and wv.strip().startswith("{"):
                    try:
                        obj = json.loads(wv)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and "poses" in obj:
                        hit = obj
                        break
        _BASELINE_CACHE["creator_pd"] = hit
    return json.loads(json.dumps(hit))


def clothes_baseline_widget_data() -> Dict[str, Any]:
    """The vendored STEP2 ClothesDesigner widget_data baseline (deep copy)."""
    hit = _BASELINE_CACHE.get("clothes_wd")
    if hit is None:
        hit = {}
        ui = load_step_ui("clothes")
        for node in ui.get("nodes", []) or []:
            if node.get("type") != "ClothesDesigner":
                continue
            for wv in node.get("widgets_values") or []:
                if isinstance(wv, str) and wv.strip().startswith("{"):
                    try:
                        obj = json.loads(wv)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and "costume_info" in obj:
                        hit = obj
                        break
        _BASELINE_CACHE["clothes_wd"] = hit
    return json.loads(json.dumps(hit))


def clothes_baseline_control() -> Tuple[str, str]:
    """(repo_id, node_state JSON string) from the vendored STEP2 ControlCenter —
    feeds /vnccs/control_center/clothes_preview's pipe builder."""
    hit = _BASELINE_CACHE.get("clothes_cc")
    if hit is None:
        repo_id, node_state = "MIUProject/VNCCS_v3.0", "{}"
        ui = load_step_ui("clothes")
        for node in ui.get("nodes", []) or []:
            if node.get("type") != "VNCCS_ControlCenter":
                continue
            wvs = node.get("widgets_values") or []
            if wvs and isinstance(wvs[0], str) and wvs[0].strip():
                repo_id = wvs[0].strip()
            for wv in wvs[1:]:
                if isinstance(wv, str) and wv.strip().startswith("{"):
                    try:
                        obj = json.loads(wv)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and ("selected_model" in obj or "loras" in obj):
                        node_state = wv
                        break
            break
        hit = (repo_id, node_state)
        _BASELINE_CACHE["clothes_cc"] = hit
    return hit


def default_pose_set() -> List[Dict[str, Any]]:
    """The 12 default VNCCS poses (deep copies) from the vendored creator graph."""
    return creator_baseline_pose_data().get("poses") or []


# The Pose Studio CSR path accepts at most 16 captured images (node-side limit).
MAX_POSE_SET = 16


def _apply_pose_set(api: Dict[str, dict], pose_set: Optional[List[Dict[str, Any]]]) -> None:
    """Replace the VNCCS_PoseStudio pose list with the caller's selection.

    Each entry is a VNCCS pose dict ({bones, modelRotation, camera?, prompt?…})
    from the default set (/pose-defaults) or the host pose library (full data).
    Runs BEFORE _inject_pose_captures so the pre-render covers exactly these.
    """
    if not pose_set:
        return
    poses = [p for p in pose_set if isinstance(p, dict)][:MAX_POSE_SET]
    if not poses:
        return
    for nid in find_nodes_by_class(api, "VNCCS_PoseStudio"):
        def mut(obj, _poses=poses):
            obj["poses"] = _poses
            # stale panel captures/cache ids would shadow the new pose list
            obj.pop("captured_images", None)
            obj.pop("capture_id", None)
            lp = obj.get("lighting_prompts")
            if isinstance(lp, list) and lp:
                fill = lp[0]
                obj["lighting_prompts"] = [
                    (p.get("prompt") if isinstance(p, dict) else "") or fill
                    for p in _poses
                ]
        patch_json_widget(api, nid, "pose_data", mut)


def _inject_pose_captures(api: Dict[str, dict]) -> None:
    """Pre-render VNCCS_PoseStudio captures app-side and inject them as
    ``captured_images`` so the node takes its CSR path.  The node's headless
    Python fallback renderer crashes on poses with non-zero modelRotation
    (upstream np.matrix bug — see pose_render.py).  Best-effort: on any
    failure the graph is submitted unchanged."""
    node_ids = find_nodes_by_class(api, "VNCCS_PoseStudio")
    if not node_ids:
        return
    try:
        from . import pose_render
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pose_render unavailable ({e}) — skipping capture injection")
        return
    for nid in node_ids:
        def mut(obj):
            if not isinstance(obj, dict) or obj.get("captured_images"):
                return
            caps = pose_render.render_pose_captures(obj)
            if not caps:
                return
            obj["captured_images"] = caps
            poses = obj.get("poses") or []
            lp = obj.get("lighting_prompts")
            if not (isinstance(lp, list) and len(lp) >= len(caps)):
                lp = [(p.get("prompt", "") if isinstance(p, dict) else "") or ""
                      for p in poses]
            obj["lighting_prompts"] = lp
        try:
            patch_json_widget(api, nid, "pose_data", mut)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"pose capture injection failed on node {nid}: {e}")


def _apply_cloner_images(api: Dict[str, dict], *, character_name: str,
                        cloner_images: List[Dict[str, Any]]) -> None:
    """Patch CharacterCloner.source_images (Step 1 Cloner) with uploaded refs.

    Each ref is ComfyUI's {name, type:"input", subfolder} from /upload/image.
    """
    if not cloner_images:
        return
    for nid in find_nodes_by_class(api, "CharacterCloner"):
        def mut(obj, _imgs=cloner_images, _n=character_name):
            obj["source_images"] = _imgs
            obj["selected_idx"] = 0
            obj.setdefault("source_images_character", _n or "Clone")
        patch_json_widget(api, nid, "widget_data", mut)


# --------------------------------------------------------------------------- #
# Public builders
# --------------------------------------------------------------------------- #
def assemble_step(
    step: str,
    object_info: dict,
    *,
    character_name: str,
    character_info: Optional[Dict[str, Any]] = None,
    gen_settings: Optional[Dict[str, Any]] = None,
    control_center: Optional[Dict[str, Any]] = None,
    generator_overrides: Optional[Dict[str, Any]] = None,
    nsfw: bool = False,
    background: str = "Green",
    # clothes (step 2)
    costume_name: Optional[str] = None,
    costume_info: Optional[Dict[str, Any]] = None,
    clone_image: Optional[Dict[str, Any]] = None,
    clone_sam_prompt: Optional[str] = None,
    # emotions (step 3)
    costumes: Optional[List[str]] = None,
    emotions: Optional[List[str]] = None,
    generation_model: str = "Anima",
    prompt_style: str = "Anima",
    # cloner (step 1 clone)
    cloner_images: Optional[List[Dict[str, Any]]] = None,
    # pose selection (creator / cloner / clothes — graphs with a Pose Studio)
    pose_set: Optional[List[Dict[str, Any]]] = None,
    tap_prefix: str = "vnccs_native",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Build a submittable API graph for a VNCCS step + its SaveImage tap map."""
    if step not in STEP_FILES:
        raise ValueError(f"unknown VNCCS step: {step}")
    ui = load_step_ui(step)
    api = ui_to_api_prompt(ui, object_info)
    _seed_hidden_widget_data(ui, api)

    if step in ("creator", "cloner"):
        ci = map_character_info(character_info or {}, name=character_name, nsfw=nsfw, background=background)
        _apply_character_form(api, ci, gen_settings)
        if step == "cloner":
            _apply_cloner_images(api, character_name=character_name, cloner_images=cloner_images or [])
    elif step == "clothes":
        _apply_clothes_form(api, character_name=character_name, costume_name=costume_name or "Costume",
                            costume_info=costume_info or {}, clone_image=clone_image,
                            clone_sam_prompt=clone_sam_prompt, background=background, gen_settings=gen_settings)
    elif step == "emotions":
        _apply_emotions_form(api, character_name=character_name, costumes=costumes or ["Original"],
                             emotions=emotions or [], generation_model=generation_model,
                             prompt_style=prompt_style, gen_settings=gen_settings)
    _apply_control_center(api, control_center)
    _apply_generator_settings(api, step, character_name=character_name, nsfw=nsfw,
                              generator_overrides=generator_overrides)
    _apply_pose_set(api, pose_set)
    _inject_pose_captures(api)

    gen_cls, taps = STEP_TAPS[step]
    gen_ids = find_nodes_by_class(api, gen_cls)
    if not gen_ids:
        raise ValueError(f"{step}: generator {gen_cls} not found in graph")
    tap_defs = [(slot, f"{tap_prefix}/{character_name}/{label}") for slot, label in taps]
    added = add_save_image_taps(api, gen_ids[0], tap_defs)
    # tap_map: label -> save_node_id
    tap_map = {label: added[f"{tap_prefix}/{character_name}/{label}"] for _, label in taps}
    return api, tap_map


def build_creator_graph(object_info: dict, **kwargs) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Step-1 Creator: generate a character from a form."""
    return assemble_step("creator", object_info, **kwargs)


def build_cloner_graph(object_info: dict, **kwargs) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Step-1 Cloner: reproduce a character from reference images (source_images set
    on the CharacterCloner widget_data by the caller via generator_overrides/form)."""
    return assemble_step("cloner", object_info, **kwargs)


def build_clothes_graph(object_info: dict, **kwargs) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Step-2 Clothes: re-dress an existing character across poses."""
    return assemble_step("clothes", object_info, **kwargs)


def build_emotions_graph(object_info: dict, **kwargs) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Step-3 Emotions: FaceDetailer re-render per (costume x emotion)."""
    return assemble_step("emotions", object_info, **kwargs)
