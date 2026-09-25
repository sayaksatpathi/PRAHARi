"""ONVIF onboarding validation — contract + a full mock-device onboarding.

The ONVIF client (`prahari/edge/sources/onvif_client.py`) is a real client on the
official ONVIF WSDL, but a *physical* ONVIF camera was not available during
development. These tests close as much of that gap as software can:

1. ``contract_check()`` proves the client is bound to the real ONVIF Device and
   Media WSDL contracts and that the onboarding operations are defined/callable —
   i.e. it is a real client, not a stub.

2. A **mock ONVIF device** that answers the exact call sequence a real camera
   answers (GetDeviceInformation -> create_media_service -> GetProfiles ->
   create_type/GetStreamUri) drives ``probe()`` end-to-end, so the onboarding
   *logic* — profile enumeration, per-profile RTSP URI extraction, result
   assembly — is validated without hardware.

The mock mirrors a Hikvision-style device (main + sub stream). Full device
interop sign-off still requires a physical camera or an ONVIF conformance tool;
the same ``probe()`` runs against one via ``scripts/onvif_probe.py --host``.
"""
from __future__ import annotations

import pytest

from prahari.edge.sources import onvif_client


# --- mock ONVIF device: answers the onboarding call sequence -----------------

class _Obj:
    """Attribute bag standing in for a zeep-generated complex type."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _MockMediaService:
    def __init__(self, host: str):
        self._host = host
        # Two profiles, as a typical camera exposes: main + sub stream.
        self._profiles = [
            _Obj(token="Profile_1", Name="mainStream"),
            _Obj(token="Profile_2", Name="subStream"),
        ]
        self._uri_by_token = {
            "Profile_1": f"rtsp://{host}:554/Streaming/Channels/101",
            "Profile_2": f"rtsp://{host}:554/Streaming/Channels/102",
        }

    def GetProfiles(self):
        return self._profiles

    def create_type(self, op_name):
        assert op_name == "GetStreamUri"
        return _Obj(ProfileToken=None, StreamSetup=None)

    def GetStreamUri(self, req):
        # A real device requires the caller to set ProfileToken + an RTSP setup.
        assert req.ProfileToken in self._uri_by_token, "ProfileToken not set correctly"
        assert req.StreamSetup and req.StreamSetup["Transport"]["Protocol"] == "RTSP"
        return _Obj(Uri=self._uri_by_token[req.ProfileToken])


class _MockDeviceMgmt:
    def GetDeviceInformation(self):
        return _Obj(Manufacturer="Hikvision", Model="DS-2CD2085FWD-I",
                    FirmwareVersion="V5.6.3", SerialNumber="TESTONVIF0001")


class MockONVIFCamera:
    """Duck-types onvif.ONVIFCamera for the onboarding path."""
    def __init__(self, host, port=80, user="", passwd="", *a, **kw):
        self.host = host
        self.devicemgmt = _MockDeviceMgmt()
        self._media = _MockMediaService(host)

    def create_media_service(self):
        return self._media


# --- tests -------------------------------------------------------------------

def test_contract_check_binds_official_wsdl():
    """The client is bound to the real ONVIF Device + Media contracts."""
    result = onvif_client.contract_check()
    assert result["contract_valid"] is True
    for op in onvif_client.REQUIRED_DEVICE_OPS:
        assert result["operations"]["device"][op] is True
    for op in onvif_client.REQUIRED_MEDIA_OPS:
        assert result["operations"]["media"][op] is True


def test_probe_onboards_a_mock_device():
    """The full onboarding sequence produces a usable camera config."""
    result = onvif_client.probe(
        "192.0.2.50", port=80, user="admin", passwd="secret",
        camera_factory=MockONVIFCamera,
    )
    assert result["reachable"] is True
    assert result["manufacturer"] == "Hikvision"
    assert result["model"] == "DS-2CD2085FWD-I"
    assert result["firmware"] == "V5.6.3"
    assert result["profiles"] == 2

    streams = {s["profile"]: s["rtsp"] for s in result["streams"]}
    assert streams["mainStream"] == "rtsp://192.0.2.50:554/Streaming/Channels/101"
    assert streams["subStream"] == "rtsp://192.0.2.50:554/Streaming/Channels/102"
    # Every stream must carry a real RTSP URI — the whole point of onboarding.
    assert all(s["rtsp"].startswith("rtsp://") for s in result["streams"])


def test_probe_substream_preferred_for_analytics():
    """A caller can pick the lighter sub-stream from onboarding output."""
    result = onvif_client.probe("192.0.2.50", camera_factory=MockONVIFCamera)
    sub = next((s for s in result["streams"] if "sub" in s["profile"].lower()), None)
    assert sub is not None
    assert sub["rtsp"].endswith("/102")
