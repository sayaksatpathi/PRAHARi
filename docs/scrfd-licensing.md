# SCRFD Licensing — Status and Resolution

**Audit item:** "SCRFD licensing." **Status: RESOLVED for deployment.**

## The question

SCRFD-500M (InsightFace) is a strong, small face detector we evaluated as a
candidate. The problem is its **weights**:

- InsightFace **code** is MIT.
- InsightFace **pretrained models are stated for non-commercial / research use
  only.**

That restriction is binding for a government deployment. Shipping the SCRFD
pretrained weights would be a licensing violation, regardless of how good the
model is.

## The resolution

Prahari does not need SCRFD to ship. The deployable face path uses
**permissively-licensed** models only:

| Backend | Licence | Role |
|---------|---------|------|
| **YuNet 2023mar** (OpenCV Zoo) | **Apache-2.0 / MIT** | **deployable default** (AP 0.626 on WIDER FACE val) |
| Haar frontal-face (OpenCV) | permissive, no external weights | always-available fallback |
| SCRFD-500M (InsightFace) | **research-only weights** | **evaluation candidate only — never deployed** |

The default face path is now `build_face_detector("auto")`, which returns YuNet
when its model is present and falls back to the Haar baseline otherwise. The
`CameraPipeline` uses this `"auto"` path, so **no deployed configuration touches
research-only weights.**

## Enforced in code (not just documented)

1. **Default is deployable.** `build_face_detector()` defaults to `"auto"` →
   YuNet/Haar. The pipeline (`prahari/edge/pipeline.py`) uses `"auto"`.
2. **SCRFD is hard-gated.** `ScrfdFaceDetector` refuses to construct unless
   research use is explicitly acknowledged — constructor flag
   `allow_research_weights=True` **or** env `PRAHARI_ALLOW_RESEARCH_WEIGHTS=1`.
   A deployment path that accidentally selects SCRFD raises a `RuntimeError`
   explaining why, instead of silently loading restricted weights.
3. **It announces itself.** When permitted (evaluation only), SCRFD logs a
   warning and `describe()` reports
   `"license": "research/non-commercial only — NOT for deployment"`.
4. **Evaluation still works.** `scripts/evaluate_widerface.py --detector scrfd`
   passes `allow_research_weights=True`, so the Haar-vs-SCRFD-vs-YuNet comparison
   remains reproducible.

## Verify it

```bash
# Deployable default — never SCRFD:
python -c "from prahari.edge.detect.face import build_face_detector as b; print(b('auto').describe()['name'])"
#   -> OpenCV YuNet 2023mar   (or Haar if the YuNet model is absent)

# SCRFD blocked by default:
python -c "from prahari.edge.detect.face import build_face_detector as b; b('scrfd')"
#   -> RuntimeError: SCRFD-500M uses research-only ... blocked from loading

# SCRFD for evaluation only:
python scripts/evaluate_widerface.py --detector scrfd   # opts in internally
```

## Remaining, honest

- SCRFD stays in the tree as a **research comparison** only; if it is ever to be
  deployed, the promotion gate in [model-provenance.md](model-provenance.md)
  applies — the weights must be procured/cleared for the deployment or retrained
  on a commercially clear source.
- The one *remaining* licence item for review is unrelated to SCRFD: the Re-ID
  weights (Market-1501 dataset terms) — tracked in
  [model-provenance.md](model-provenance.md).

See also: [model-provenance.md](model-provenance.md),
[bill-of-materials.md](bill-of-materials.md) §4 (software BOM).
