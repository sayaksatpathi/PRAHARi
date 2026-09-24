"""Compare ANPR OCR backends on REAL Indian plates (#9): fast-alpr global OCR vs
EasyOCR on upscaled plate crops. EasyOCR is markedly better on Indian plates.
Ground truth is hand-labelled from the images (var/anpr_india_results.json + this)."""
import warnings, json, difflib; warnings.filterwarnings("ignore")
import cv2, numpy as np
from pathlib import Path
from fast_alpr import ALPR
import easyocr
alpr = ALPR(detector_model="yolo-v9-t-384-license-plate-end2end", ocr_model="global-plates-mobile-vit-v2-model")
reader = easyocr.Reader(["en"], gpu=False, verbose=False)
gt = {"car_10.jpeg":"KL55R2473","car_0.jpeg":"MH15TC554","car_5.jpeg":"KA51MJ8156","car_30.jpeg":"GJ05JD9759"}
def norm(s): return "".join(c for c in (s or "").upper() if c.isalnum())
rows={}
for img_path in sorted(Path("data/testing/anpr_india").glob("*.jpeg")):
    img=cv2.imread(str(img_path))
    res=alpr.predict(img)
    if not res or not res[0].detection: continue
    b=res[0].detection.bounding_box
    crop=img[max(0,int(b.y1)):int(b.y2), max(0,int(b.x1)):int(b.x2)]
    if crop.size==0: continue
    crop=cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    out=reader.readtext(crop, detail=1, paragraph=False)
    txt=norm("".join(t for _,t,c in sorted(out,key=lambda x:-x[2])[:3]))
    rows[img_path.name]=txt
    print(img_path.name,"-> EasyOCR:",txt)
# accuracy on labeled
ex=0; rr=[]
for k,g in gt.items():
    r=norm(rows.get(k,"")); g=norm(g); s=difflib.SequenceMatcher(None,g,r).ratio(); rr.append(s); ex+=(g==r)
    print(f"  {k}: GT {g} | easyocr {r} | sim {s:.2f}")
print("EasyOCR Indian (n=%d): exact %d/%d, mean char-sim %.2f"%(len(gt),ex,len(gt),sum(rr)/len(rr)))
json.dump(rows, open("var/anpr_easyocr.json","w"), indent=1)
