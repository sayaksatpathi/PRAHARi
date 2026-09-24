"""Run Prahari's real ANPR (fast-alpr) on REAL Indian vehicle images.

Addresses judge critique #9 (ANPR validated only on Chinese CCPD plates): this
runs the actual fast-alpr backend on real Indian plates and records readings.
Ground truth is established by visual inspection of the annotated outputs.
"""
import json, warnings, sys
from pathlib import Path
warnings.filterwarnings("ignore")
import cv2
from fast_alpr import ALPR

ROOT = Path(__file__).resolve().parents[1]
IMGS = ROOT / "data/testing/anpr_india"
OUT = ROOT / "var/anpr_india"; OUT.mkdir(parents=True, exist_ok=True)

a = ALPR(detector_model="yolo-v9-t-384-license-plate-end2end",
         ocr_model="global-plates-mobile-vit-v2-model")
rows = []
for img_path in sorted(IMGS.glob("*.jpeg")):
    img = cv2.imread(str(img_path))
    if img is None:
        continue
    res = a.predict(img)
    reads = []
    for x in res:
        txt = x.ocr.text if x.ocr else None
        c = x.ocr.confidence if x.ocr else None
        conf = float(sum(c) / len(c)) if isinstance(c, (list, tuple)) and c else (float(c) if c else 0.0)
        reads.append({"text": txt, "conf": round(conf, 3)})
        if x.detection:
            b = x.detection.bounding_box
            cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), (0, 220, 0), 2)
            cv2.putText(img, f"{txt} {conf:.2f}", (int(b.x1), max(20, int(b.y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(OUT / (img_path.stem + "_anpr.jpg")), img)
    rows.append({"image": img_path.name, "plates_detected": len(res), "reads": reads})
    print(f"{img_path.name}: {len(res)} plate(s) -> " +
          ", ".join(f"{r['text']}({r['conf']})" for r in reads) if reads else f"{img_path.name}: none")

(ROOT / "var/anpr_india_results.json").write_text(json.dumps(rows, indent=2))
det = sum(1 for r in rows if r["plates_detected"] > 0)
print(f"\n{det}/{len(rows)} images had a plate detected. Annotated: var/anpr_india/")
