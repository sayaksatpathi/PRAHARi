"""Standard Market-1501 evaluation of a pretrained Re-ID ONNX model (#3).

Implements the standard single-query protocol (junk removal, CMC + mAP) with the
model's OWN preprocessing, so a strong pretrained model (OSNet) can be compared
fairly to Prahari's current ResNet-18 (Rank-1 0.705).

    python scripts/eval_reid_osnet.py --model models/reid_osnet.onnx
"""
import argparse, re, time
from pathlib import Path
import numpy as np, cv2, onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
MK = ROOT / "datasets/market1501/Market-1501-v15.09.15"
_PAT = re.compile(r"([-\d]+)_c(\d+)")
MEAN = np.array([0.485,0.456,0.406],np.float32); STD = np.array([0.229,0.224,0.225],np.float32)

def parse(folder):
    out=[]
    for p in sorted(Path(folder).glob("*.jpg")):
        m=_PAT.search(p.name)
        if not m: continue
        pid=int(m.group(1)); cam=int(m.group(2))
        out.append((str(p),pid,cam))
    return out

def prep(img):  # OSNet: 256x128, RGB, ImageNet norm
    img=cv2.resize(img,(128,256)); img=cv2.cvtColor(img,cv2.COLOR_BGR2RGB).astype(np.float32)/255.0
    img=(img-MEAN)/STD
    return img.transpose(2,0,1)

def feats(sess, items, bs=128):
    inp=sess.get_inputs()[0].name; out=[]
    for i in range(0,len(items),bs):
        batch=np.stack([prep(cv2.imread(f)) for f,_,_ in items[i:i+bs]]).astype(np.float32)
        f=sess.run(None,{inp:batch})[0]
        f=f/ (np.linalg.norm(f,axis=1,keepdims=True)+1e-12)
        out.append(f)
    return np.concatenate(out)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--model",required=True); a=ap.parse_args()
    q=parse(MK/"query"); g=parse(MK/"bounding_box_test")
    print(f"query {len(q)} gallery {len(g)}",flush=True)
    sess=ort.InferenceSession(a.model,providers=["CPUExecutionProvider"])
    t=time.time(); qf=feats(sess,q); gf=feats(sess,g); print("features %.0fs"%(time.time()-t),flush=True)
    qpid=np.array([p for _,p,_ in q]); qcam=np.array([c for _,_,c in q])
    gpid=np.array([p for _,p,_ in g]); gcam=np.array([c for _,_,c in g])
    dist=1-qf@gf.T
    aps=[]; cmc1=0; n=0
    for i in range(len(q)):
        order=np.argsort(dist[i])
        keep=~((gpid[order]==qpid[i]) & (gcam[order]==qcam[i]))  # remove same id+cam
        keep &= (gpid[order]!=-1)                                 # remove junk
        good=(gpid[order]==qpid[i])[keep]
        if not good.any(): continue
        n+=1; cmc1+= int(good[0])
        # AP
        idx=np.where(good)[0]; ap_=0.0
        for k,gi in enumerate(idx): ap_+=(k+1)/(gi+1)
        aps.append(ap_/len(idx))
    print("VALID queries %d | Rank-1 %.4f | mAP %.4f"%(n, cmc1/n, np.mean(aps)))

if __name__=="__main__":
    main()
