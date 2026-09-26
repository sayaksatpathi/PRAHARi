"""Signed model manifest — supply-chain integrity for model weights.

A silently swapped model file is a real attack: replace `models/yolo.onnx` with a
weights file that ignores a class and the node goes blind to it while every health
indicator stays green. This module verifies each model's SHA-256 against a manifest
before it is loaded, and the manifest itself can be HMAC-signed so it cannot be
edited to match a tampered model.

Manifest format (models/manifest.json):

    { "models": { "yolox_s.onnx": "<sha256>", ... },
      "signature": "<hmac-sha256 over the sorted models map>" }   # optional

A plain ``{ "name": "<sha256>", ... }`` map is also accepted. The signing key comes
from the env var ``PRAHARI_MANIFEST_KEY``; without it, hashes are still verified
(tamper-evident) but the manifest is not signature-protected.

Generate with ``scripts/generate_model_manifest.py``; verified via ``EdgeFactory``
(prahari/edge/factory.py) and in ``build_detector``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
SIG_KEY_ENV = "PRAHARI_MANIFEST_KEY"
MODEL_SUFFIXES = (".onnx", ".pt", ".pth")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_map(manifest: dict[str, Any]) -> dict[str, str]:
    """Extract the {name: sha256} map from either accepted manifest shape."""
    if isinstance(manifest, dict) and isinstance(manifest.get("models"), dict):
        return {k: str(v) for k, v in manifest["models"].items()}
    return {k: str(v) for k, v in manifest.items()
            if k != "signature" and isinstance(v, str)}


def load_manifest(model_dir: str | Path) -> dict[str, Any]:
    p = Path(model_dir) / MANIFEST_NAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:                                # noqa: BLE001
        raise ValueError(f"Model integrity failed: manifest at {p} is not valid JSON")


def verify_model(model_path: str | Path, manifest: dict[str, Any],
                 *, strict: bool = False) -> None:
    """Raise ValueError('Model integrity failed: ...') on a hash mismatch.

    A model not listed in the manifest passes unless ``strict`` (so partial
    manifests do not block unrelated files), but a *listed* model whose hash does
    not match always fails.
    """
    model_path = Path(model_path)
    hashes = hash_map(manifest)
    name = model_path.name
    if name not in hashes:
        if strict:
            raise ValueError(f"Model integrity failed: {name} is not listed in the manifest")
        return
    actual = sha256_file(model_path)
    expected = hashes[name]
    if not hmac.compare_digest(actual, expected):
        raise ValueError(
            f"Model integrity failed: {name} sha256 {actual[:12]}… does not match "
            f"the manifest {str(expected)[:12]}…")


def sign_models(models: dict[str, str], key: str) -> str:
    payload = json.dumps(models, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


def verify_signature(manifest: dict[str, Any], key: str | None = None) -> bool:
    key = key or os.environ.get(SIG_KEY_ENV)
    if not key or "signature" not in manifest:
        return False
    return hmac.compare_digest(str(manifest.get("signature", "")),
                               sign_models(hash_map(manifest), key))


def generate_manifest(model_dir: str | Path, key: str | None = None) -> dict[str, Any]:
    model_dir = Path(model_dir)
    models = {p.name: sha256_file(p) for p in sorted(model_dir.iterdir())
              if p.is_file() and p.suffix.lower() in MODEL_SUFFIXES}
    manifest: dict[str, Any] = {"models": models}
    key = key or os.environ.get(SIG_KEY_ENV)
    if key:
        manifest["signature"] = sign_models(models, key)
    return manifest
