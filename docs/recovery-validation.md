# Recovery Validation

This document verifies the robustness of Prahari under component, network, and disk stress scenarios.

## 1. Network Failure & Recovery
- **Camera Pipeline Supervisor**: If an RTSP stream drops, freezes, or the camera reboots, the `CameraPipeline` crashes safely. The `_pipeline_supervisor` loop running in `app.py` catches this exit, executes an exponential backoff (starting at 5s, maxing at 60s), and reinitializes the connection autonomously. Repeated failures of one camera do not affect the execution of other cameras running on the same node.
- **Offline Sync Resiliency**: If the Edge Node loses WAN connectivity to the Core Server, locally detected events, generated `.mp4` evidence clips, and telemetry are safely buffered to the local SQLite WAL and filesystem. Once the network is restored, `SyncManager` iteratively uploads the backlog via idempotent ID-keyed pushes, preventing duplicate events.

## 2. Disk & Data Preservation
- **Retention Pruning**: Prahari prevents Edge-node disk exhaustion by running a daily background asyncio task (`EvidenceStore.prune`) that deletes evidence files older than the configured `evidence_retention_days`. The SQLite database maintains the metadata but the raw bytes are reclaimed.
- **Live Backups**: SQLite's Safe Backup API is exposed via `scripts/backup_db.py`, allowing operators to snapshot the node's configuration, rules, and event ledger safely while the pipeline is actively writing frames, avoiding SQLITE_BUSY or corruption states.
