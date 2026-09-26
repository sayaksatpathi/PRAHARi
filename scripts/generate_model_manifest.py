"""Generate (and optionally sign) the model integrity manifest.

Writes models/manifest.json with the SHA-256 of every model weight, so the node can
verify a model has not been swapped before loading it. Set PRAHARI_MANIFEST_KEY to
also HMAC-sign the manifest (so it cannot be edited to match a tampered model).

    python scripts/generate_model_manifest.py
    PRAHARI_MANIFEST_KEY=<secret> python scripts/generate_model_manifest.py   # signed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prahari.edge import manifest as m           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    out = args.out or (args.model_dir / m.MANIFEST_NAME)
    man = m.generate_manifest(args.model_dir)
    out.write_text(json.dumps(man, indent=2))
    signed = "signature" in man
    print(f"wrote {out}  ({len(man['models'])} models, "
          f"{'signed' if signed else 'unsigned — set ' + m.SIG_KEY_ENV + ' to sign'})")
    for name, h in man["models"].items():
        print(f"  {name:32} {h[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
