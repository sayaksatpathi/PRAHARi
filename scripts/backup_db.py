"""Backup and restore utilities for the Prahari Edge Database.

SQLite in WAL mode requires special handling to backup safely while the node
is running. This tool uses the SQLite Backup API to snapshot the database
and WAL without taking the system offline or risking corruption.
"""
import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("prahari.backup")

def backup_db(src_path: str, dst_path: str) -> None:
    src_p = Path(src_path)
    if not src_p.exists():
        log.error("Source database %s does not exist", src_path)
        sys.exit(1)
        
    dst_p = Path(dst_path)
    if dst_p.exists():
        log.error("Destination %s already exists", dst_path)
        sys.exit(1)

    log.info("Starting safe snapshot of %s -> %s", src_path, dst_path)
    
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        
        with src, dst:
            src.backup(dst, pages=256, sleep=0.1)
            
        src.close()
        dst.close()
        log.info("Backup complete successfully.")
    except Exception as e:
        log.error("Backup failed: %s", e)
        sys.exit(1)

def restore_db(src_path: str, dst_path: str) -> None:
    src_p = Path(src_path)
    if not src_p.exists():
        log.error("Backup source %s does not exist", src_path)
        sys.exit(1)
        
    dst_p = Path(dst_path)
    if dst_p.exists():
        log.warning("Destination %s exists. It will be overwritten.", dst_path)
        
    log.info("Restoring %s -> %s", src_path, dst_path)
    
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        
        with src, dst:
            src.backup(dst, pages=256, sleep=0.1)
            
        src.close()
        dst.close()
        log.info("Restore complete successfully.")
    except Exception as e:
        log.error("Restore failed: %s", e)
        sys.exit(1)

def main() -> None:
    parser = argparse.ArgumentParser(description="Prahari Edge Database Backup Tool")
    subparsers = parser.add_subparsers(dest="action", required=True)
    
    backup_parser = subparsers.add_parser("backup", help="Safely backup a running database")
    backup_parser.add_argument("src", help="Path to live database (e.g., var/edge.db)")
    backup_parser.add_argument("dst", help="Path for the new backup file")
    
    restore_parser = subparsers.add_parser("restore", help="Restore a database from a backup")
    restore_parser.add_argument("src", help="Path to backup file")
    restore_parser.add_argument("dst", help="Path to destination database (e.g., var/edge.db)")
    
    args = parser.parse_args()
    
    if args.action == "backup":
        backup_db(args.src, args.dst)
    elif args.action == "restore":
        restore_db(args.src, args.dst)

if __name__ == "__main__":
    main()
