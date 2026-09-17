# Security Validation

This document summarizes the validation of critical security controls implemented in Prahari.

## 1. Authentication & API Protection
- **Role-Based Access Control (RBAC)**: Enforced via `Depends(require("role"))` using JWT Bearer tokens on FastAPI routes.
- **WebSocket Security**: The `/ws` endpoint verifies token signatures during the initial upgrade handshake and rejects unauthenticated observers, preventing telemetry eavesdropping.
- **Evidence Access**: All `/api/events/.../evidence` endpoints demand a valid `viewer` token minimum, blocking unauthorized evidence crawling.

## 2. Evidence Integrity & Chain of Custody
- **SHA-256 Hashing**: Every event maintains a cryptographic hash of its bounding box metadata, the frame JPEG bytes, and the generated `.mp4` clip.
- **Evidence Export**: Authorised `admin` users can trigger a `/export` which packages the event JSON, evidence metadata, media, and node provenance into a unified `.zip`.
- **Independent Verification**: A standalone CLI `scripts/verify_evidence.py` can be executed on any air-gapped machine to parse an exported `.zip`, recompute SHA-256 hashes of the media, and cryptographically prove the evidence was unaltered since the edge node originally captured it.

## 3. Storage Security
- **Credential Storage**: Physical camera passwords (e.g. for RTSP/ONVIF streams) are encrypted symmetrically using Fernet before being written to the SQLite database.
- **Model Integrity**: The edge loader expects a `manifest.json` file detailing the SHA-256 of the model weights. Altered models trigger a `ValueError` during boot, preventing silent supply-chain tampering.
