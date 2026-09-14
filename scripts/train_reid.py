"""Train a person re-identification embedding, and export it to ONNX.

Offline only. PyTorch is a training dependency and never reaches the edge node,
which loads the exported ONNX graph through the `onnxruntime` session it already
has.

    python scripts/prepare_reid_dataset.py
    python scripts/train_reid.py --epochs 20
    python scripts/evaluate_reid.py            # measures against the HSV baseline

**The loss.** Identity classification plus a batch-hard triplet term on the
embedding. Classification alone produces features that separate the *training*
identities and need not separate anyone else; the triplet term optimises the
distance structure directly, which is what the coordinator actually consumes.
Batch-hard - the furthest positive and nearest negative within each batch - is
used because random triplets are overwhelmingly easy after the first epoch and
stop contributing gradient.

**The backbone.** ImageNet-pretrained ResNet-18 by default. With ~500 identities
from seven scenes there is not enough data to learn general visual features from
scratch, and a from-scratch network on this set would memorise rather than
generalise. `--backbone small` trains a compact network instead, for comparison
and for a smaller export.

**What success means here.** Not the training accuracy - the identities are
disjoint, so validation is on people the model has never seen, and the number
that matters is whether it beats the HSV histogram it would replace. That
comparison is `scripts/evaluate_reid.py`, and if the model loses, the histogram
stays. A learned cue that is not measurably better is just a bigger dependency.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EMBED_DIM = 128


def build_model(backbone: str, num_classes: int, embed_dim: int):
    import torch.nn as nn
    import torchvision

    if backbone == "resnet18":
        net = torchvision.models.resnet18(weights="IMAGENET1K_V1")
        feature_dim = net.fc.in_features
        net.fc = nn.Identity()
    else:
        # A compact from-scratch alternative: ~1.2M parameters, for when the
        # export size matters more than the last few points of accuracy.
        net = nn.Sequential(
            nn.Conv2d(3, 32, 3, 2, 1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.Conv2d(32, 64, 3, 2, 1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.Conv2d(64, 128, 3, 2, 1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
            nn.Conv2d(128, 256, 3, 2, 1), nn.BatchNorm2d(256), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        )
        feature_dim = 256

    class ReidNet(nn.Module):
        """Backbone -> embedding (what ships) -> classifier (training only)."""

        def __init__(self):
            super().__init__()
            self.backbone = net
            self.embedding = nn.Sequential(
                nn.Linear(feature_dim, embed_dim), nn.BatchNorm1d(embed_dim))
            self.classifier = nn.Linear(embed_dim, num_classes)

        def forward(self, x, with_logits: bool = False):
            features = self.embedding(self.backbone(x))
            if with_logits:
                return features, self.classifier(features)
            return features

    return ReidNet()


def batch_hard_triplet(embeddings, labels, margin: float):
    """Batch-hard triplet loss on L2-normalised embeddings."""
    import torch
    import torch.nn.functional as F

    normed = F.normalize(embeddings, dim=1)
    distance = torch.cdist(normed, normed, p=2)

    same = labels.unsqueeze(0) == labels.unsqueeze(1)
    eye = torch.eye(len(labels), dtype=torch.bool, device=labels.device)
    positive_mask = same & ~eye
    negative_mask = ~same

    # An anchor with no positive in this batch contributes nothing; masking with
    # -inf/+inf and then filtering keeps those rows out of the mean rather than
    # letting them inject a spurious zero.
    hardest_positive = (distance.masked_fill(~positive_mask, float("-inf"))
                        .max(dim=1).values)
    hardest_negative = (distance.masked_fill(~negative_mask, float("inf"))
                        .min(dim=1).values)
    valid = positive_mask.any(dim=1) & negative_mask.any(dim=1)
    if not valid.any():
        return embeddings.sum() * 0.0
    return F.relu(hardest_positive[valid] - hardest_negative[valid] + margin).mean()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=Path("datasets/reid"))
    parser.add_argument("--out", type=Path, default=Path("models/reid.onnx"))
    parser.add_argument("--backbone", default="resnet18",
                        choices=["resnet18", "small"])
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--margin", type=float, default=0.3)
    parser.add_argument("--triplet-weight", type=float, default=1.0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader
    from torchvision import transforms
    from torchvision.datasets import ImageFolder

    manifest_path = args.data / "manifest.json"
    if not manifest_path.exists():
        print(f"No dataset at {args.data}. Run scripts/prepare_reid_dataset.py first.")
        return 1

    device = ("cuda" if (args.device in ("auto", "cuda") and torch.cuda.is_available())
              else "cpu")
    if args.device == "cuda" and device == "cpu":
        print("CUDA requested but unavailable; training on CPU.")
    print(f"training on {device}")
    if device == "cuda":
        free, total = torch.cuda.mem_get_info()
        print(f"  GPU: {torch.cuda.get_device_name(0)}, "
              f"{free/2**20:.0f} MB free of {total/2**20:.0f} MB")

    normalise = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    # Augmentation aimed at what actually varies between two cameras watching the
    # same person: horizontal flip (different approach direction), small crops
    # and colour jitter (different exposure and white balance), and random
    # erasing (partial occlusion, which is constant in crowded footage).
    train_transform = transforms.Compose([
        transforms.Resize((128, 64)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomApply([transforms.ColorJitter(0.25, 0.25, 0.2, 0.02)], 0.6),
        transforms.RandomCrop((128, 64), padding=8, padding_mode="edge"),
        transforms.ToTensor(), normalise,
        transforms.RandomErasing(p=0.4, scale=(0.02, 0.2)),
    ])
    eval_transform = transforms.Compose([
        transforms.Resize((128, 64)), transforms.ToTensor(), normalise,
    ])

    train_set = ImageFolder(args.data / "train", transform=train_transform)
    val_set = ImageFolder(args.data / "val", transform=eval_transform)
    print(f"  train {len(train_set)} crops / {len(train_set.classes)} identities")
    print(f"  val   {len(val_set)} crops / {len(val_set.classes)} identities "
          f"(disjoint from train)")

    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.workers, drop_last=True,
                              pin_memory=(device == "cuda"))

    model = build_model(args.backbone, len(train_set.classes), EMBED_DIM).to(device)
    params = sum(p.numel() for p in model.parameters())
    print(f"  {args.backbone}: {params/1e6:.2f}M parameters, {EMBED_DIM}-d embedding")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=5e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, args.epochs)
    cross_entropy = nn.CrossEntropyLoss(label_smoothing=0.1)

    peak_vram = 0.0
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        model.train()
        totals = {"loss": 0.0, "id": 0.0, "tri": 0.0, "correct": 0, "n": 0}
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            embeddings, logits = model(images, with_logits=True)
            id_loss = cross_entropy(logits, labels)
            tri_loss = batch_hard_triplet(embeddings, labels, args.margin)
            loss = id_loss + args.triplet_weight * tri_loss

            optimiser.zero_grad(set_to_none=True)
            loss.backward()
            optimiser.step()

            totals["loss"] += loss.item() * len(labels)
            totals["id"] += id_loss.item() * len(labels)
            totals["tri"] += tri_loss.item() * len(labels)
            totals["correct"] += (logits.argmax(1) == labels).sum().item()
            totals["n"] += len(labels)
        schedule.step()
        if device == "cuda":
            peak_vram = max(peak_vram, torch.cuda.max_memory_allocated() / 2**20)

        n = max(1, totals["n"])
        print(f"  epoch {epoch:>3}/{args.epochs}  loss {totals['loss']/n:.4f} "
              f"(id {totals['id']/n:.4f} tri {totals['tri']/n:.4f})  "
              f"train-acc {totals['correct']/n:.3f}", flush=True)

    elapsed = time.perf_counter() - started
    print(f"\ntrained in {elapsed/60:.1f} min"
          + (f", peak VRAM {peak_vram:.0f} MB" if device == "cuda" else ""))

    # --- export ---------------------------------------------------------
    # Only the embedding path is exported. The classifier exists to shape the
    # features and is meaningless off this identity set, so shipping it would be
    # dead weight and an invitation to misuse.
    model.eval().cpu()

    class EmbedOnly(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, x):
            return torch.nn.functional.normalize(self.inner(x), dim=1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, 3, 128, 64)
    torch.onnx.export(
        EmbedOnly(model), dummy, str(args.out),
        input_names=["crop"], output_names=["embedding"],
        dynamic_axes={"crop": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
    )
    size_mb = args.out.stat().st_size / 1e6
    print(f"exported {args.out} ({size_mb:.1f} MB)")

    (args.out.with_suffix(".json")).write_text(json.dumps({
        "backbone": args.backbone, "embedding_dim": EMBED_DIM,
        "crop": [64, 128], "epochs": args.epochs,
        "train_identities": len(train_set.classes),
        "val_identities": len(val_set.classes),
        "parameters_m": round(params / 1e6, 2),
        "size_mb": round(size_mb, 1),
        "trained_on": "MOT17 train, ground-truth boxes",
        "device": device,
        "peak_vram_mb": round(peak_vram) if device == "cuda" else None,
        "train_minutes": round(elapsed / 60, 1),
        "limitation": ("Daylight pedestrian imagery only. Improves matching "
                       "between cameras of similar modality; does not address "
                       "visible-to-thermal re-identification."),
    }, indent=2))

    print("\nNow measure it against the cue it would replace:")
    print("    python scripts/evaluate_reid.py")
    print("If it does not beat the HSV histogram, keep the histogram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
