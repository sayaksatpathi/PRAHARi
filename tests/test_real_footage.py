import pytest
from pathlib import Path

from prahari.edge.sources.stream import StreamSource
from prahari.common.models import Camera

@pytest.fixture
def sample_real_frame():
    path = Path("data/MOT17/train/MOT17-02-FRCNN/img1/000001.jpg")
    if not path.exists():
        pytest.skip("MOT17 sequence not downloaded for real footage regression.")
    return path

def test_real_footage_regression(sample_real_frame):
    """
    Test:
        Uses an actual MOT17 frame to verify pipeline decoding.
    """
    cam = Camera(
        camera_id="CAM-REAL-TEST",
        name="Real Footage Cam",
        stream_url=str(sample_real_frame)
    )
    
    source = StreamSource(
        camera_id=cam.camera_id,
        url=cam.stream_url
    )
    assert source.open(), "Failed to open local image as stream"
    
    frame = source.read()
    assert frame is not None, "Failed to decode frame"
    assert frame.image is not None
    source.close()
