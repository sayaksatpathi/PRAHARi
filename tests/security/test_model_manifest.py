import pytest
import json
import hashlib
from prahari.edge.factory import EdgeFactory

def test_model_manifest_mismatch_raises_value_error(tmp_path):
    model_path = tmp_path / "fake_model.onnx"
    model_path.write_bytes(b"fake model bytes")
    
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "fake_model.onnx": "wrong_hash_123"
    }))
    
    factory = EdgeFactory(tmp_path)
    
    with pytest.raises(ValueError, match="Model integrity failed"):
        factory.detector(model_path)
