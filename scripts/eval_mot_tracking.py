"""CLEAR-MOT / IDF1 on held-out MOT17-02/04 for a given ONNX detector.

Runs detector -> ByteTracker -> CLEAR-MOT over the whole sequence (no profiling
gate), so the MOTA/IDF1 reflect the detector+tracker directly. Use to compare
detectors on the same held-out sequences.

    python scripts/eval_mot_tracking.py --model models/yolov8n_ft.onnx
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from prahari.common.config import get_settings
from prahari.common.models import ObjectClass
from prahari.edge.detect.onnx_yolo import OnnxYoloDetector
from prahari.edge.track.bytetrack import ByteTracker
from prahari.eval.mot import MotSequenceSource
from prahari.eval.tracking import TrackingScorer, ground_truth_as_pairs, tracks_as_pairs

def eval_seq(seq_dir, model, device, conf):
    src = MotSequenceSource(seq_dir); src.open()
    det = OnnxYoloDetector(model_path=model, device=device, conf_threshold=conf)
    s = get_settings()
    trk = ByteTracker(seq_dir.name, match_iou=s.track_match_iou,
                      max_lost_frames=int(s.track_timeout_seconds * src.nominal_fps))
    scorer = TrackingScorer(); i = 0
    while True:
        fr = src.read()
        if fr is None: break
        dets = det.infer(fr.image, allowed=None, frame_index=i)
        tracks = trk.update(dets, fr.timestamp, fr.timestamp.timestamp())
        if fr.ground_truth:
            scorer.update(ground_truth_as_pairs(fr.ground_truth),
                          tracks_as_pairs([t for t in tracks if t.object_class is ObjectClass.PERSON]))
        i += 1
    src.close()
    return scorer.as_dict()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--root", default="datasets/MOT17/train")
    ap.add_argument("--seqs", default="MOT17-02-FRCNN,MOT17-04-FRCNN")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    t0 = time.time(); rows = []
    for name in a.seqs.split(","):
        d = eval_seq(Path(a.root) / name, a.model, a.device, a.conf)
        rows.append((name, d))
        print(f"{name}: MOTA {d['mota']:.3f} IDF1 {d['idf1']:.3f} "
              f"IDsw {d.get('id_switches')} frames {d.get('frames_scored')}", flush=True)
    # frame-weighted aggregate
    tot = sum(r[1].get("frames_scored", 0) for r in rows) or 1
    mota = sum(r[1]["mota"] * r[1].get("frames_scored", 0) for r in rows) / tot
    idf1 = sum(r[1]["idf1"] * r[1].get("frames_scored", 0) for r in rows) / tot
    res = {"model": a.model, "aggregate": {"mota": round(mota, 4), "idf1": round(idf1, 4)},
           "per_sequence": {n: d for n, d in rows}, "runtime_sec": round(time.time() - t0, 1)}
    print(f"AGGREGATE: MOTA {mota:.3f} | IDF1 {idf1:.3f}")
    if a.out: Path(a.out).write_text(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
