"""Independent offline verifier for exported Prahari evidence bundles."""

import argparse
import json
import logging
import sys
import zipfile
import hashlib
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("prahari.verify")

def verify_bundle(zip_path: Path) -> bool:
    if not zip_path.exists():
        log.error("Bundle %s not found.", zip_path)
        return False
        
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            files = zf.namelist()
            if "manifest.json" not in files:
                log.error("Missing manifest.json in evidence bundle.")
                return False
                
            manifest_bytes = zf.read("manifest.json")
            manifest = json.loads(manifest_bytes)
            
            # Verify hashes in manifest against actual file contents in bundle
            all_valid = True
            for file_key, expected_hash in manifest.get("hashes", {}).items():
                if file_key in files:
                    file_bytes = zf.read(file_key)
                    actual_hash = hashlib.sha256(file_bytes).hexdigest()
                    if actual_hash != expected_hash:
                        log.error("Hash mismatch for %s: expected %s, got %s", 
                                  file_key, expected_hash, actual_hash)
                        all_valid = False
                    else:
                        log.info("Verified %s (SHA-256 matched).", file_key)
                else:
                    log.warning("Manifest expects %s, but it's missing from the bundle.", file_key)
            
            if "event.json" in files:
                event = json.loads(zf.read("event.json"))
                log.info("Event ID: %s", event.get("event_id"))
                log.info("Event Type: %s", event.get("event_type"))
                
            if "provenance.json" in files:
                prov = json.loads(zf.read("provenance.json"))
                log.info("Exported from Node: %s by %s at %s", 
                         prov.get("node_id"), prov.get("exported_by"), prov.get("timestamp"))

            if all_valid:
                log.info("EVIDENCE VALID. All cryptographic hashes match.")
            else:
                log.error("EVIDENCE INVALID. Cryptographic tampering detected.")
            
            return all_valid
            
    except Exception as e:
        log.exception("Error verifying bundle: %s", e)
        return False

def main():
    parser = argparse.ArgumentParser(description="Verify Prahari offline evidence bundle integrity")
    parser.add_argument("bundle", type=Path, help="Path to exported evidence .zip file")
    args = parser.parse_args()
    
    success = verify_bundle(args.bundle)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
