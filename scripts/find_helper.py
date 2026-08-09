"""find_helper.py -- locate the RBMN worker helper (port 8765) on the LAN.

The training box dropped off the network overnight and may have come back on a
different DHCP address. Parallel TCP-connect scan of 192.168.12.0/24 on 8765
(and 8188 for ComfyUI while we're at it), then /health on any hit.
"""
import concurrent.futures as cf
import json
import socket
import urllib.request

SUBNET = "192.168.12."
from helper_token import helper_token as _helper_token  # v1.276.4: token out of source
TOKEN = _helper_token()


def probe(ip_port):
    ip, port = ip_port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        s.connect((ip, port))
        return ip, port
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


def main():
    targets = [(f"{SUBNET}{i}", p) for i in range(1, 255) for p in (8765, 8188)]
    hits = []
    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(probe, targets):
            if r:
                hits.append(r)
    if not hits:
        print("no host on the subnet answers on 8765 or 8188")
        return 1
    for ip, port in sorted(hits):
        line = f"OPEN {ip}:{port}"
        if port == 8765:
            try:
                with urllib.request.urlopen(
                        f"http://{ip}:8765/health?token={TOKEN}", timeout=5) as r:
                    h = json.loads(r.read().decode("utf-8"))
                line += (f"  helper v{h.get('helper')} host={h.get('host')} "
                         f"gpu={h.get('gpu', {}).get('name')} "
                         f"comfy_listening={h.get('comfy', {}).get('listening')}")
            except Exception as e:  # noqa: BLE001
                line += f"  (health failed: {e})"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
