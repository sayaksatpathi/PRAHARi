import pytest
from prahari.edge.sources.stream import StreamSource
from prahari.common.models import Camera

@pytest.mark.asyncio
async def test_real_rtsp_pipeline_integration():
    """
    Test:
        RTSP connection mock
    """
    pytest.skip("MediaMTX not running locally; skipping RTSP live test.")
