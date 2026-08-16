import json
import sys
import urllib.parse
import urllib.request

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

H = "192.168.12.201"
NODES = ["RandomNoise", "KSamplerSelect", "SamplerCustomAdvanced", "ManualSigmas",
         "LTXVConcatAVLatent", "LTXVSeparateAVLatent", "LTXVLatentUpsampler",
         "LTXVImgToVideoInplace", "LTXVPreprocess", "ResizeImageMaskNode",
         "EmptyLTXVLatentVideo", "LTXVEmptyLatentAudio", "LTXVConditioning",
         "LTXVDualCFGGuider", "VAEDecodeTiled", "LTXVAudioVAEDecode",
         "CreateVideo", "SaveVideo", "LatentUpscaleModelLoader"]
for n in NODES:
    try:
        d = json.load(urllib.request.urlopen(
            f"http://{H}:8188/object_info/{urllib.parse.quote(n)}", timeout=20))
        spec = d[n]["input"]
        parts = []
        for sec in ("required", "optional"):
            for k, v in (spec.get(sec) or {}).items():
                t = v[0] if isinstance(v, (list, tuple)) and v else "?"
                dv = ""
                if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], dict) and "default" in v[1]:
                    dv = f"={json.dumps(v[1]['default'])[:24]}"
                parts.append(f"{k}:{t if isinstance(t, str) else 'CHOICE'}{dv}" + ("(opt)" if sec == "optional" else ""))
        print(n, "|", ", ".join(parts))
    except Exception as e:
        print(n, "ERR", repr(e)[:100])
