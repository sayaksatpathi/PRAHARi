# Prahari Operational Gap Audit

This document catalogues the missing operational features required to transition Prahari from a prototype to a production-ready edge analytics node, as identified during the engineering audit.

## P0: Security & Core Correctness (MUST FIX)

### 1. Authenticated WebSocket
- **Current State:** The `/ws` endpoint in `app.py` is unauthenticated and accepts any connection.
- **Affected Files:** `prahari/edge/app.py`
- **Risk:** High. Anyone with network access can stream the raw internal telemetry of the edge node.
- **Implementation Plan:** Require a JWT token in the WebSocket connection (e.g. via subprotocols or query param since WS doesn't easily support headers natively, with token validation immediately upon connect).
- **Test Plan:** Attempt connection with missing, invalid, and expired tokens. Validate rejection.

### 2. Authenticated Evidence Access
- **Current State:** `/api/events/{event_id}/evidence/{kind}` is unauthenticated.
- **Affected Files:** `prahari/edge/app.py`
- **Risk:** High. Anyone can pull sensitive imagery without an operator session.
- **Implementation Plan:** Add `Depends(current_principal)` to the endpoint.
- **Test Plan:** `pytest` tests validating 401 on unauthorized evidence fetches.

### 3. Authenticated Edge-to-Core Ingestion
- **Current State:** `SyncManager` in `sync.py` pushes to the core without mTLS or JWT.
- **Affected Files:** `prahari/edge/sync.py`, `config.py`
- **Risk:** High. Core can be spoofed, or edge nodes can be spoofed by attackers to inject fake events.
- **Implementation Plan:** Provision an edge-node JWT or mTLS certs and inject them into the `httpx.AsyncClient`.
- **Test Plan:** Validate HTTP headers sent to the mock core during sync tests.

### 4. HTTPS/mTLS
- **Current State:** The system runs on plain HTTP.
- **Affected Files:** `prahari/edge/app.py`, `scripts/run_node.sh`
- **Risk:** High. Tokens and telemetry are sent in plaintext.
- **Implementation Plan:** Configure Uvicorn to use SSL cert/key if provided in the environment.
- **Test Plan:** Verify the startup script successfully binds to HTTPS.

### 5. Encrypted Credential Storage
- **Current State:** `Camera.password` and `Camera.username` are stored as plaintext JSON in `db.py`.
- **Affected Files:** `prahari/common/db.py`, `prahari/common/models.py`
- **Risk:** High. A stolen SQLite file gives the attacker access to the physical cameras.
- **Implementation Plan:** Implement Fernet symmetric encryption in `Database.upsert_camera` and `get_camera`. Use a key from the environment.
- **Test Plan:** Write a raw SQL query in a test to assert that the `payload` column doesn't contain the plaintext password.

### 6. Signed Model Manifest/Hash Verification
- **Current State:** `factory.py` loads whatever model weights are present in the directory.
- **Affected Files:** `prahari/edge/factory.py`
- **Risk:** Medium. Supply-chain attack where model weights are silently swapped to blind the system to certain classes.
- **Implementation Plan:** Store a signed `manifest.json` with expected SHA256 hashes of the `.onnx` and `.pt` files. Verify before loading.
- **Test Plan:** Tamper with a model file in tests and assert `prahari` refuses to boot.

### 9. ONVIF Camera Validation
- **Current State:** UNVALIDATED.
- **Affected Files:** `prahari/edge/sources/onvif.py` (Missing)
- **Risk:** High for physical deployments requiring WSDL-based PTZ and config negotiation.
- **Implementation Plan:** Requires physical ONVIF test cameras or hardware-in-the-loop mock server. Currently defaults to explicit RTSP URIs.

### 7. Real Face Detection
- **Current State:** IMPLEMENTED & TESTED.
- **Affected Files:** `prahari/edge/pipeline.py`, `prahari/edge/factory.py`
- **Risk:** Low for general perimeter, high for chokepoints.
- **Implementation Plan:** Integrate a lightweight face detector (e.g. YOLO-Face) if the `Capability.FACE_DETECTION` is granted.
- **Test Plan:** Push a test image with faces and verify `ObjectClass.PERSON` detections get associated face bounding boxes.

### 8. Explicit Night Movement Event
- **Current State:** Missing from `EventType`.
- **Affected Files:** `prahari/common/models.py`, `prahari/edge/alerting.py`
- **Risk:** Medium. Operators can't easily filter for nighttime activity.
- **Implementation Plan:** Add `EventType.NIGHT_MOVEMENT`. Trigger when general movement is detected during low-light hours.
- **Test Plan:** Simulate a nighttime hour and assert `NIGHT_MOVEMENT` fires.

### 9. Explicit Suspicious Activity Event
- **Current State:** Missing from `EventType`.
- **Affected Files:** `prahari/common/models.py`, `prahari/edge/alerting.py`
- **Risk:** Medium. Complex behavioural anomalies aren't explicitly flagged.
- **Implementation Plan:** Add `EventType.SUSPICIOUS_ACTIVITY`.
- **Test Plan:** Ensure the scoring logic emits this event.

### 10. Persistent Incident Model
- **Current State:** Events exist, but there is no overarching `Incident` grouping them.
- **Affected Files:** `prahari/common/models.py`, `prahari/common/db.py`
- **Risk:** Low for raw function, high for operator workflow.
- **Implementation Plan:** Create an `Incident` model and `incidents` table. Link `Event` to `Incident`.
- **Test Plan:** Validate incident CRUD.

### 11. Database Migrations
- **Current State:** Hardcoded schema creation in `db.py` (v2). No `ALTER TABLE` logic.
- **Affected Files:** `prahari/common/db.py`
- **Risk:** High. Any schema change currently breaks existing deployments.
- **Implementation Plan:** Add a lightweight migration runner checking `user_version` or `schema_meta` and executing SQL deltas.
- **Test Plan:** Create a v1 mock DB and assert the app successfully upgrades it to v2/v3.

### 12. Pipeline Startup/Reconnect Supervisor
- **Current State:** A pipeline that crashes or loses stream permanently never restarts.
- **Affected Files:** `prahari/edge/app.py`, `prahari/edge/pipeline.py`
- **Risk:** High. Silent failure of analytics for a camera.
- **Implementation Plan:** Wrap `CameraPipeline.start()` in a supervisor loop with backoff.
- **Test Plan:** Inject a synthetic exception into `CameraPipeline` and assert it restarts.

### 13. Scheduled Retention
- **Current State:** Edge node never deletes data unless disk is full during sync.
- **Affected Files:** `prahari/edge/app.py`, `prahari/common/db.py`
- **Risk:** High. Edge disk fills up with synced evidence, breaking new captures.
- **Implementation Plan:** Add an `asyncio` loop that drops evidence older than X days.
- **Test Plan:** Mock time and ensure old clips are deleted.

### 14. Correct Current-Rate Normalcy Model
- **Current State:** `normalcy_total` only counts historically, missing real-time drift.
- **Affected Files:** `prahari/common/db.py`, `prahari/edge/normalcy.py`
- **Risk:** Medium. Normalcy becomes too rigid.
- **Implementation Plan:** Factor in a decay rate for older normalcy counts.
- **Test Plan:** Add counts and assert they decay appropriately.

### 15. Deterministic Simulator Seed
- **Current State:** Simulator generates random trajectories.
- **Affected Files:** `prahari/edge/sources/simulator.py`
- **Risk:** Low. E2E tests are flaky.
- **Implementation Plan:** Seed the random generator.
- **Test Plan:** Run twice, assert trajectories match perfectly.

### 16. Real ONVIF Semantics
- **Current State:** Missing.
- **Affected Files:** `prahari/edge/sources/stream.py`
- **Risk:** Medium. PTZ and precise configuration can't be fetched.
- **Implementation Plan:** Integrate ONVIF WSDL or `zeep` client stubs.
- **Test Plan:** Mock ONVIF server, test capability extraction.

### 17. Security/Failure Integration Tests
- **Current State:** Exist for evidence and auth, but not comprehensive for P0.
- **Affected Files:** `tests/security/`
- **Risk:** High.
- **Implementation Plan:** Add tests for websocket auth, edge-node auth, config encryption.
- **Test Plan:** N/A (self-evident).

---

## P1: Fleet & Enterprise (Planned)
- Node Enrollment
- Fleet Management
- Persistent Node Health
- Persistent Cross-Camera Entities
- Persistent Plate Sightings
- Topology CRUD
- Configuration Versioning/Rollback
- User Administration
- Token/Session Revocation
- Authentication Rate Limiting
- Evidence Export + Offline Verification
- Backup/Restore
- Signed Update/Rollback
- CI/CD
- Dependency Lockfile
- SBOM/Security Audit
- Browser E2E Tests

## P2: Domain Specifics (Planned)
- Border-Domain Validation Dataset
- Field ANPR Validation
- Night/IR Validation
- Vehicle/Cattle Validation
- Re-ID Domain Recalibration
- Cross-Node Correlation
- Thermal Validation
- Sensor Fusion
