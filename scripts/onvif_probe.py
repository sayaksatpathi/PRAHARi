"""ONVIF probe / contract-check for Prahari camera onboarding (#10).

    python scripts/onvif_probe.py                       # offline contract check
    python scripts/onvif_probe.py --host 192.168.1.10 --user admin --passwd x
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prahari.edge.sources import onvif_client

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host"); ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--user", default=""); ap.add_argument("--passwd", default="")
    a = ap.parse_args()
    if a.host:
        try:
            r = onvif_client.probe(a.host, a.port, a.user, a.passwd)
        except Exception as e:
            r = {"reachable": False, "error": str(e)[:200]}
    else:
        r = onvif_client.contract_check()
    print(json.dumps(r, indent=2))
    Path("var/onvif_probe.json").write_text(json.dumps(r, indent=2))

if __name__ == "__main__":
    main()
