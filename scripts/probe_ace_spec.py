import json, urllib.request

u = "http://192.168.12.201:8188/object_info/TextEncodeAceStepAudio1.5"
d = json.load(urllib.request.urlopen(u, timeout=20))
spec = d["TextEncodeAceStepAudio1.5"]["input"]
for sec in ("required", "optional"):
    for k, v in (spec.get(sec) or {}).items():
        print(sec, "|", k, "|", json.dumps(v)[:220])
