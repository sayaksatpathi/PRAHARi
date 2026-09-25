"""India-specific thermal FACE-domain validation (KKWETC), ready-to-run.

Scope, stated up front and NOT overstated: KKWETC is an Indian thermal *face*
database (816 visible images of 68 subjects + 150 thermal images of 50 subjects,
thermal captured with a FLIR C2, in India). It is a face-recognition dataset, NOT
an Indian pedestrian RGB<->LWIR surveillance Re-ID dataset. So this validates:

    "India-specific thermal FACE-domain behaviour of the deployable face detector"

and must NOT be read as "Indian border thermal Re-ID validated".

What it measures: run the deployable face detector (YuNet, Apache/MIT, the default
via build_face_detector('auto')) on the Indian thermal face images and report the
detection rate — i.e. how well a visible-trained face detector transfers to Indian
thermal faces. A low rate is the honest, expected domain-gap finding and motivates
a thermal face model; it is India-relevant evidence without overclaiming.

KKWETC is research-gated (request from the authors; see the paper "'KKWETC' Indian
Face Database", IJETT 2017). It is not downloadable in-session. Place the thermal
images under <root> (any folder of thermal face JPEGs) and this runs. Until then it
prints the access procedure and exits cleanly.

    python scripts/benchmark_kkwetc_thermal_face.py --root datasets/kkwetc/thermal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACCESS = (
    "KKWETC is research-gated. Request it from the authors of \"'KKWETC' Indian "
    "Face Database\" (IJETT, 2017; ResearchGate). Once obtained, put the thermal "
    "face images (any nested folders of .jpg/.png) under --root and re-run. This "
    "is India-specific thermal FACE-domain validation only, not border Re-ID."
)


def find_images(root: Path) -> list[Path]:
    exts = (".jpg", ".jpeg", ".png", ".bmp")
    return [p for p in root.rglob("*") if p.suffix.lower() in exts]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT / "datasets/kkwetc/thermal")
    ap.add_argument("--out", type=Path, default=ROOT / "var/kkwetc_thermal_face.json")
    args = ap.parse_args()

    if not args.root.exists() or not find_images(args.root):
        print("KKWETC thermal face images not found at", args.root)
        print(ACCESS)
        return 1

    import cv2
    from prahari.edge.detect.face import build_face_detector

    images = find_images(args.root)
    detectors = {"yunet (deployable default)": build_face_detector("yunet"),
                 "haar (baseline)": build_face_detector("haar")}

    results = {}
    for name, det in detectors.items():
        found = 0
        total_faces = 0
        read = 0
        for p in images:
            img = cv2.imread(str(p))
            if img is None:
                continue
            read += 1
            faces = det.detect(img)
            if faces:
                found += 1
                total_faces += len(faces)
        rate = found / max(read, 1)
        results[name] = {
            "images_read": read,
            "images_with_a_face": found,
            "detection_rate": round(rate, 4),
            "avg_faces_per_image": round(total_faces / max(read, 1), 3),
        }
        print(f"  {name:<28} detection rate {rate:.3f} "
              f"({found}/{read} images)")

    out = {
        "task": "India-specific thermal FACE-domain validation",
        "dataset": "KKWETC Indian thermal face database (FLIR C2), thermal split",
        "scope": ("Face-domain only. NOT Indian border thermal Re-ID. Measures how a "
                  "visible-trained face detector transfers to Indian thermal faces."),
        "detectors": results,
        "finding": ("Detection rate on thermal faces quantifies the visible->thermal "
                    "face-domain gap for the deployable detector; a thermal face "
                    "model is the fix if the rate is low."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
