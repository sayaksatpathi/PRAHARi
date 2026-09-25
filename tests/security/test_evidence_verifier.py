import pytest
import zipfile
import json
import hashlib
import io
import subprocess
import sys
from pathlib import Path
from prahari.common.models import Event
from datetime import datetime, timezone

def test_verify_evidence_success(tmp_path):
    zip_path = tmp_path / "valid_bundle.zip"
    
    # Mock event data
    event = {
        "event_id": "evt-123",
        "event_type": "loitering",
        "camera_id": "cam-1"
    }
    
    # Create fake media
    frame_bytes = b"mock frame bytes"
    frame_hash = hashlib.sha256(frame_bytes).hexdigest()
    
    clip_bytes = b"mock clip bytes"
    clip_hash = hashlib.sha256(clip_bytes).hexdigest()
    
    # Create manifest
    manifest = {
        "event_id": "evt-123",
        "hashes": {
            "frame.jpg": frame_hash,
            "clip.mp4": clip_hash
        }
    }
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("event.json", json.dumps(event))
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("frame.jpg", frame_bytes)
        zf.writestr("clip.mp4", clip_bytes)
        
    script_path = Path(__file__).parent.parent.parent / "scripts" / "verify_evidence.py"
    result = subprocess.run([sys.executable, str(script_path), str(zip_path)], capture_output=True, text=True)
    
    assert result.returncode == 0
    assert "EVIDENCE VALID" in result.stderr

def test_verify_evidence_tamper_failure(tmp_path):
    zip_path = tmp_path / "tampered_bundle.zip"
    
    frame_bytes = b"mock frame bytes"
    frame_hash = hashlib.sha256(frame_bytes).hexdigest()
    
    clip_bytes = b"tampered clip bytes"
    # But manifest has the OLD hash
    clip_hash = hashlib.sha256(b"mock clip bytes").hexdigest()
    
    manifest = {
        "hashes": {
            "frame.jpg": frame_hash,
            "clip.mp4": clip_hash
        }
    }
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("frame.jpg", frame_bytes)
        zf.writestr("clip.mp4", clip_bytes)
        
    script_path = Path(__file__).parent.parent.parent / "scripts" / "verify_evidence.py"
    result = subprocess.run([sys.executable, str(script_path), str(zip_path)], capture_output=True, text=True)
    
    assert result.returncode == 1
    assert "EVIDENCE INVALID" in result.stderr
