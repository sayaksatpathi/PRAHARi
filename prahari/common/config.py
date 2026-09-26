"""Central configuration for Prahari.

Every operational threshold lives here and is overridable through the
environment, because a threshold that is right for a floodlit gate camera is
wrong for a 200 m forest approach. Nothing operational is hard-coded.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRAHARI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Node identity -----------------------------------------------------
    node_id: str = "BOP-EDGE-01"
    node_name: str = "Border Outpost Edge Node 01"
    sector: str = "SECTOR-NORTH"

    # --- Server ------------------------------------------------------------
    # 8420 rather than the conventional 8000: Windows hosts running Hyper-V/WSL
    # reserve large dynamic TCP ranges (commonly 7986-8185), and binding inside
    # one fails with WinError 10013, which reads like a permissions problem and
    # wastes an afternoon. Check with:
    #     netsh int ipv4 show excludedportrange protocol=tcp
    host: str = "127.0.0.1"
    port: int = 8420
    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None

    # --- Storage -----------------------------------------------------------
    data_dir: Path = Path("./var")
    db_path: Path = Path("./var/prahari-edge.db")
    evidence_dir: Path = Path("./var/evidence")

    # --- Inference ---------------------------------------------------------
    detector: str = "auto"          # auto | onnx | synthetic
    model_path: Path = Path("./models/yolo.onnx")
    # Inference resolution. 0 reads it from the model's own input shape, which
    # is right for a fixed-size export. An export with dynamic spatial dims has
    # no size to read, and the same weights at a different input resolution is
    # a different accuracy/cost point worth measuring - so it is settable.
    detector_input_size: int = 0
    device: str = "auto"            # auto | cpu | cuda
    # Windows only. ONNX Runtime's CUDA provider loads the CUDA runtime by name
    # at session creation, and since Python 3.8 PATH is not searched for
    # extension-module DLLs. Point this at a directory holding cudart64_12.dll,
    # cublas64_12.dll and cudnn64_9.dll; left empty, prahari.common.cuda looks
    # in the usual places. See that module for why the failure mode matters.
    cuda_dll_dir: Path | None = None
    inference_interval: int = 2     # run detector every Nth frame
    detection_confidence: float = 0.35
    nms_iou: float = 0.45

    # --- ANPR --------------------------------------------------------------
    # auto | fast_alpr | synthetic. "auto" prefers the real ONNX backend and
    # falls back to the clearly-labelled synthetic reader if it is unavailable.
    anpr_backend: str = "auto"
    anpr_repeat_window_hours: float = 168.0
    anpr_repeat_min_sightings: int = 3

    # --- Segmentation (event-triggered second-stage refinement) -----------
    # auto | sam_onnx | grabcut | off. "auto" uses SAM 2 ONNX when its weights
    # are present, else GrabCut (CPU, no download). Invoked only for events at or
    # above segment_min_priority, never on the per-frame hot path.
    segment_backend: str = "auto"
    segment_min_priority: str = "high"
    sam_encoder_path: Path = Path("./models/sam2_encoder.onnx")
    sam_decoder_path: Path = Path("./models/sam2_decoder.onnx")

    # --- Cross-camera appearance -------------------------------------------
    # A learned re-ID embedding, used when present. Absent, the coordinator uses
    # the HSV histogram cue, which is tested and needs no download - the same
    # arrangement as SAM 2 and GrabCut.
    reid_model_path: Path = Path("./models/reid_osnet_market.onnx")  # OSNet 0.947; falls back to reid.onnx / HSV if absent

    # --- Tracking ----------------------------------------------------------
    track_timeout_seconds: float = 5.0
    track_match_iou: float = 0.30

    # --- Rules -------------------------------------------------------------
    loitering_threshold_seconds: float = 30.0
    intrusion_confidence: float = 0.50
    group_size_threshold: int = 3
    group_window_seconds: float = 20.0
    event_cooldown_seconds: float = 30.0

    # --- Evidence ----------------------------------------------------------
    event_clip_before_seconds: float = 3.0
    event_clip_after_seconds: float = 7.0
    evidence_retention_days: int = 30

    # --- Store-and-forward -------------------------------------------------
    core_url: str = "http://127.0.0.1:9000"
    core_token: str = ""
    sync_enabled: bool = True
    sync_retry_seconds: float = 10.0
    sync_batch_size: int = 25
    queue_max_bytes: int = 2 * 1024 * 1024 * 1024

    # --- Operator trust ----------------------------------------------------
    alert_budget_per_hour: int = 20

    # --- Auth --------------------------------------------------------------
    secret_key: str = "dev-only-insecure-key-change-me"
    token_ttl_seconds: int = 8 * 3600

    # --- Evidence notary (core-side countersigning) ------------------------
    # Held ONLY by the sector core, never distributed to edge nodes. It is what
    # lets the core witness a node's chain head so the node cannot later rewrite
    # history the core has already seen (tamper-evident -> tamper-resistant).
    # Must be overridden in production via PRAHARI_CORE_NOTARY_SECRET.
    core_notary_secret: str = "dev-only-insecure-notary-key-change-me"

    # --- Demo --------------------------------------------------------------
    demo_mode: bool = True
    demo_seed: int = Field(default=20260913, description="Deterministic demo RNG seed")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
