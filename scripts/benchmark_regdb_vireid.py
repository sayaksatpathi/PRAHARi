"""True RGB<->LWIR cross-modal Re-ID on a REAL paired dataset (RegDB protocol).

This is the genuine visible<->thermal Re-ID evaluation (not the colour-removal
stress test in benchmark_crossmodal_reid.py). It runs the standard RegDB protocol
on ACTUAL paired visible + thermal images of the same identities:

    Visible -> Thermal   Rank-1 / mAP
    Thermal -> Visible   Rank-1 / mAP

and prints the visible-only baseline alongside, so the modality gap is explicit.

RegDB layout (either is accepted):
  * idx protocol:   <root>/idx/test_visible_{trial}.txt, test_thermal_{trial}.txt
                    (each line "relative/path label"); results averaged over trials.
  * folder layout:  <root>/Visible/<id>/*.bmp and <root>/Thermal/<id>/*.bmp

RegDB is research-gated (copyright form / email mangye16@gmail.com; the mirror on
HF Demonz43/Reg_DB is visible-only and NOT sufficient). Provide the complete paired
dataset under --root and this produces the true number. Until then the honest
interim measure is the colour-removal proxy (benchmark_crossmodal_reid.py), clearly
labelled as a stress test.

    python scripts/benchmark_regdb_vireid.py --root datasets/regdb/RegDB
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_reid_market as erm            # _Box, vectors_for      # noqa: E402


def load_idx(root: Path, trial: int, split: str, modality: str):
    """Read <root>/idx/{split}_{modality}_{trial}.txt -> [(path, label)]."""
    f = root / "idx" / f"{split}_{modality}_{trial}.txt"
    if not f.exists():
        return None
    items = []
    for line in f.read_text().strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            items.append((root / parts[0], int(parts[1])))
    return items


def load_folders(root: Path, modality: str):
    """Fallback: <root>/<Modality>/<id>/*.{bmp,jpg,png} -> [(path, label)]."""
    base = None
    for name in (modality, modality.capitalize(), modality.upper()):
        if (root / name).is_dir():
            base = root / name
            break
    if base is None:
        return None
    items = []
    for id_dir in sorted(base.iterdir()):
        if not id_dir.is_dir():
            continue
        try:
            label = int(id_dir.name)
        except ValueError:
            continue
        for p in id_dir.iterdir():
            if p.suffix.lower() in (".bmp", ".jpg", ".jpeg", ".png"):
                items.append((p, label))
    return items or None


def embed_set(items, embedder):
    """[(path,label)] -> (features NxD, labels N) using the Re-ID model."""
    import cv2
    feats, labels = [], []
    for path, label in items:
        img = cv2.imread(str(path))
        if img is None:
            continue
        v = embedder.embed(img, erm._Box(img.shape[1], img.shape[0]))
        if v is not None:
            feats.append(v)
            labels.append(label)
    return np.asarray(feats, dtype=np.float32), np.asarray(labels)


def cmc_map(qf, ql, gf, gl):
    """Rank-1 and mAP with no camera constraint (RegDB has one cam per modality)."""
    if len(qf) == 0 or len(gf) == 0:
        return 0.0, 0.0
    sim = qf @ gf.T
    rank1 = 0
    aps = []
    for i in range(len(qf)):
        order = np.argsort(-sim[i], kind="stable")
        hits = (gl[order] == ql[i])
        if not hits.any():
            continue
        if hits[0]:
            rank1 += 1
        pos = np.flatnonzero(hits) + 1
        aps.append(float((np.arange(1, len(pos) + 1) / pos).mean()))
    n = max(1, len(aps))
    return rank1 / n, float(np.mean(aps)) if aps else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT / "datasets/regdb/RegDB")
    ap.add_argument("--model", type=Path, default=ROOT / "models/reid.onnx")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--out", type=Path, default=ROOT / "var/regdb_vireid_benchmark.json")
    args = ap.parse_args()

    if not args.root.exists():
        print(f"FAIL: RegDB not found at {args.root}. RegDB is research-gated; see "
              "the module docstring. The colour-removal proxy "
              "(benchmark_crossmodal_reid.py) is the honest interim measure.")
        return 1
    if not args.model.exists():
        print(f"FAIL: Re-ID model not found: {args.model}")
        return 1

    from prahari.common.config import get_settings
    from prahari.edge.crosscam import reid
    settings = get_settings()
    embedder = reid.ReidEmbedder(
        args.model, device=getattr(settings, "device", "auto"),
        cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))

    # Gather test sets — prefer idx trials, fall back to folder layout (1 trial).
    trials = []
    for t in range(1, args.trials + 1):
        vis = load_idx(args.root, t, "test", "visible")
        the = load_idx(args.root, t, "test", "thermal")
        if vis and the:
            trials.append((vis, the))
    if not trials:
        vis = load_folders(args.root, "Visible")
        the = load_folders(args.root, "Thermal")
        if not (vis and the):
            print(f"FAIL: no paired visible+thermal data under {args.root}. "
                  "Found only one modality? RegDB must include BOTH Visible and "
                  "Thermal (the HF Demonz43/Reg_DB mirror is visible-only).")
            return 1
        trials = [(vis, the)]

    v2t_r1, v2t_map, t2v_r1, t2v_map = [], [], [], []
    for vis, the in trials:
        vf, vl = embed_set(vis, embedder)
        tf, tl = embed_set(the, embedder)
        r1, mp = cmc_map(vf, vl, tf, tl); v2t_r1.append(r1); v2t_map.append(mp)
        r1, mp = cmc_map(tf, tl, vf, vl); t2v_r1.append(r1); t2v_map.append(mp)

    def avg(x):
        return round(float(np.mean(x)), 4)

    result = {
        "task": "true RGB<->LWIR cross-modal Re-ID (RegDB protocol)",
        "dataset": f"RegDB (real paired visible+thermal), {len(trials)} trial(s)",
        "model": args.model.name + " (VISIBLE-trained ResNet-18; not a VI-ReID model)",
        "device": embedder.device,
        "visible_to_thermal": {"rank1": avg(v2t_r1), "map": avg(v2t_map)},
        "thermal_to_visible": {"rank1": avg(t2v_r1), "map": avg(t2v_map)},
        "note": ("Measured with the existing VISIBLE-trained embedder, so these are "
                 "the honest cross-modal numbers of the current model, not of a "
                 "dedicated VI-ReID model. Training a VI-ReID model (e.g. AGW/DDAG "
                 "on RegDB/SYSU) is the roadmap step to raise them."),
        "baseline_same_modality_market1501": {"rank1": 0.705, "map": 0.485},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print("=== True RGB<->LWIR Re-ID (RegDB) ===")
    print(f"  Visible -> Thermal : rank-1 {result['visible_to_thermal']['rank1']} "
          f"mAP {result['visible_to_thermal']['map']}")
    print(f"  Thermal -> Visible : rank-1 {result['thermal_to_visible']['rank1']} "
          f"mAP {result['thermal_to_visible']['map']}")
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
