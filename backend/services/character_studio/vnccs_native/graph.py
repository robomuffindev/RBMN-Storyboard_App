"""Convert VNCCS UI workflow graphs to ComfyUI /prompt API format, faithfully.

VNCCS ships its Step graphs (``vnccs/workflows/*.json``) in ComfyUI *UI* format
(``nodes``/``links`` arrays with positional ``widgets_values``).  ComfyUI's
``/prompt`` queue wants *API* format (node-id keyed dict of ``class_type`` +
named ``inputs``).  The mapping from a positional ``widgets_values`` array to
named inputs requires the node's input *definition order*, which only the
worker knows — so we fetch its ``/object_info`` at dispatch time and use it here
(no guessing at widget names/order).

This mirrors ComfyUI's own ``graphToPrompt``:
  * a node input that has a link becomes ``[origin_node_id, origin_slot]``;
  * remaining widget-type inputs (in object_info order) are filled positionally
    from ``widgets_values`` (skipping the phantom ``control_after_generate``
    slot that follows a seed INT).

We then add ``SaveImage`` taps on the generator's image outputs so the results
come back through ``/history`` (the VNCCS generator normally writes to worker
folders with no SaveImage node), and expose helpers to patch the JSON ``*_data``
widgets from our form values before submitting.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# UI node types that carry no compute and must be resolved through, not emitted.
_PASSTHROUGH_TYPES = {"Reroute", "Reroute (rgthree)"}
_SKIP_TYPES = {"Note", "MarkdownNote", "PrimitiveNode", "Primitive"}

# Connection (non-widget) socket types — these are never filled from widgets_values.
_CONNECTION_TYPES = {
    "IMAGE", "LATENT", "MODEL", "CLIP", "VAE", "CONDITIONING", "MASK",
    "CONTROL_NET", "CLIP_VISION", "STYLE_MODEL", "GLIGEN", "UPSCALE_MODEL",
    "SAMPLER", "SIGMAS", "GUIDER", "NOISE", "AUDIO", "VNCCS_PIPE", "*",
}
_CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}


def _object_info_inputs(info: dict) -> List[Tuple[str, Any, dict]]:
    """Return ordered [(name, type_spec, opts)] for required then optional inputs.

    NOTE: the "hidden" section is deliberately excluded, so hidden inputs like the
    VNCCS meganodes' ``widget_data`` (CharacterCreatorV2 / CharacterCloner /
    ClothesDesigner declare it hidden) are NOT carried over by ui_to_api_prompt —
    they are (re)built from scratch by the matching ``_apply_*`` patch in
    workflows.py.  Any NEW step graph that uses a hidden-widget_data node MUST get
    its own _apply_* patch or the node will silently run with default/blank data.
    """
    out: List[Tuple[str, Any, dict]] = []
    inp = (info or {}).get("input", {}) or {}
    for section in ("required", "optional"):
        for name, spec in (inp.get(section, {}) or {}).items():
            type_spec = spec[0] if isinstance(spec, (list, tuple)) and spec else spec
            opts = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            out.append((name, type_spec, opts))
    return out


def _is_widget_input(type_spec: Any, opts: dict) -> bool:
    """A widget input is one filled from widgets_values (INT/FLOAT/STRING/BOOLEAN/COMBO list)."""
    if opts.get("forceInput"):
        return False
    if isinstance(type_spec, list):   # COMBO (list of choices) -> widget
        return True
    if isinstance(type_spec, str):
        return type_spec not in _CONNECTION_TYPES
    return False


def _build_link_map(ui_wf: dict) -> Dict[int, Tuple[int, int]]:
    """link_id -> (origin_node_id, origin_slot).  UI link: [id, oid, oslot, tid, tslot, type]."""
    lm: Dict[int, Tuple[int, int]] = {}
    for l in ui_wf.get("links", []) or []:
        if isinstance(l, (list, tuple)) and len(l) >= 5:
            lm[l[0]] = (l[1], l[2])
    return lm


def ui_to_api_prompt(ui_wf: dict, object_info: dict) -> Dict[str, dict]:
    """Convert a ComfyUI UI workflow dict to an API /prompt dict using object_info.

    Reroute nodes are resolved through to their upstream source.  Nodes whose
    class isn't in object_info still convert (link inputs + positional widgets),
    but a warning is logged since widget ordering can't be verified.
    """
    linkmap = _build_link_map(ui_wf)
    nodes = {n["id"]: n for n in ui_wf.get("nodes", [])}

    def resolve_source(node_id: int, slot: int) -> Optional[Tuple[str, int]]:
        """Follow Reroute chains to the real producing node."""
        n = nodes.get(node_id)
        if n is None:
            return None
        if n.get("type") in _PASSTHROUGH_TYPES:
            # reroute has a single input; follow it
            for inp in n.get("inputs", []) or []:
                link = inp.get("link")
                if link is not None and link in linkmap:
                    oid, oslot = linkmap[link]
                    return resolve_source(oid, oslot)
            return None
        return (str(node_id), slot)

    api: Dict[str, dict] = {}
    for nid, n in nodes.items():
        cls = n.get("type")
        if cls in _PASSTHROUGH_TYPES or cls in _SKIP_TYPES:
            continue
        info = object_info.get(cls)
        if info is None:
            logger.warning("ui_to_api_prompt: no object_info for %r; converting best-effort", cls)

        inputs_out: Dict[str, Any] = {}
        linked_names = set()
        for inp in n.get("inputs", []) or []:
            link = inp.get("link")
            if link is not None and link in linkmap:
                oid, oslot = linkmap[link]
                src = resolve_source(oid, oslot)
                if src is not None:
                    inputs_out[inp["name"]] = [src[0], src[1]]
                    linked_names.add(inp["name"])

        wv = n.get("widgets_values")
        if isinstance(wv, list) and wv and info is not None:
            # ALL widget-type inputs in definition order, plus which are linked.
            all_widgets: List[Tuple[str, Any, bool]] = []
            for name, tspec, opts in _object_info_inputs(info):
                if _is_widget_input(tspec, opts):
                    all_widgets.append((name, tspec, name in linked_names))
            total = len(all_widgets)
            nonlinked = [w for w in all_widgets if not w[2]]
            # Two ComfyUI export conventions: converted widgets either KEEP a
            # placeholder value in widgets_values (len==total) or are REMOVED
            # (len==#nonlinked).  Pick the walk order that matches the length.
            if len(wv) >= total and total > 0:
                walk = all_widgets            # keep-mode: index over all widgets
            else:
                walk = nonlinked              # remove-mode: only non-linked widgets
            wi = 0
            for name, tspec, is_linked in walk:
                if wi >= len(wv):
                    break
                val = wv[wi]
                wi += 1
                # skip phantom control_after_generate slot following a seed INT
                if tspec == "INT" and wi < len(wv) and wv[wi] in _CONTROL_VALUES:
                    wi += 1
                if not is_linked:             # linked inputs use the connection, not the widget value
                    inputs_out[name] = val
        elif isinstance(wv, list) and wv:
            # No object_info: best-effort — map non-linked widget values by order
            # is impossible without names; leave widget inputs unset (rare path).
            logger.warning("ui_to_api_prompt: %s has widgets_values but no object_info; widgets left unset", cls)

        api[str(nid)] = {"class_type": cls, "inputs": inputs_out}
    return api


# --------------------------------------------------------------------------- #
# Post-conversion helpers
# --------------------------------------------------------------------------- #
def find_nodes_by_class(api: Dict[str, dict], class_type: str) -> List[str]:
    return [nid for nid, n in api.items() if n.get("class_type") == class_type]


def patch_json_widget(api: Dict[str, dict], node_id: str, widget: str,
                      mutate: Callable[[dict], None]) -> None:
    """Load a node's JSON-string widget, apply ``mutate(obj)`` in place, re-dump."""
    node = api.get(node_id)
    if not node:
        raise KeyError(f"node {node_id} not in graph")
    raw = node["inputs"].get(widget)
    obj = {}
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {}
    mutate(obj)
    node["inputs"][widget] = json.dumps(obj)


def add_save_image_taps(api: Dict[str, dict], source_node_id: str,
                        taps: List[Tuple[int, str]], start_id: int = 9000) -> Dict[str, str]:
    """Add SaveImage nodes on ``source_node_id``'s image output slots.

    ``taps`` = [(output_slot, filename_prefix), ...].  Returns {prefix: save_node_id}.
    """
    added: Dict[str, str] = {}
    nid = start_id
    for slot, prefix in taps:
        sid = str(nid)
        api[sid] = {
            "class_type": "SaveImage",
            "inputs": {"images": [source_node_id, slot], "filename_prefix": prefix},
        }
        added[prefix] = sid
        nid += 1
    return added
