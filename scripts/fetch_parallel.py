"""Fetch one large file over several concurrent HTTP range requests.

Measured on this link: a single connection to any host sustains ~46 KB/s, while
eight concurrent connections to the *same* host sustain ~607 KB/s aggregate. The
bottleneck is per-connection throttling rather than available bandwidth, so the
fix for a single large file is to request it in parallel byte ranges and
reassemble.

The guards from `fetch_datasets.py` apply here for the same reasons, learned the
same way:

*   **A lock**, so two downloaders cannot share one destination. That failure
    corrupted a 5.8 GB archive once already, silently, and the file looked
    finished.
*   **Per-part verification** — each range must come back the exact length it
    was asked for, and `206`, not `200`. A server that ignores Range returns the
    whole file for every part, and concatenating those yields a plausibly-sized
    file of overlapping garbage.
*   **Whole-file verification** — final size against `Content-Length`, and for
    archives, that the archive actually opens. A corrupt download should be
    caught here, not hours later by whatever consumes it.

    python scripts/fetch_parallel.py <url> --out datasets/file.zip
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKERS = 8


def fetch(url: str, dest: Path, workers: int) -> int:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    lock = dest.with_suffix(dest.suffix + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"A download of {dest.name} is already in progress. If that "
              f"process is dead, delete {lock} and re-run.")
        return 3
    with os.fdopen(fd, "w") as fh:
        fh.write(f"pid {os.getpid()}")

    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            head = client.head(url)
            head.raise_for_status()
            total = int(head.headers.get("content-length", 0))
            if not total:
                print("Server did not report a size; cannot split into ranges.")
                return 1
            if head.headers.get("accept-ranges", "").lower() != "bytes":
                print("Server does not advertise range support; "
                      "falling back to one connection.")
                workers = 1

        print(f"{dest.name}: {total/1e6:.1f} MB over {workers} connection(s)")
        if dest.exists() and dest.stat().st_size == total:
            print("  already present and the right size")
            return verify(dest, total)

        chunk = (total + workers - 1) // workers
        parts = [(i, i * chunk, min(total - 1, (i + 1) * chunk - 1))
                 for i in range(workers)]
        parts = [p for p in parts if p[1] <= p[2]]
        done = [0] * len(parts)
        started = time.perf_counter()

        def one(part):
            index, start, end = part
            part_path = dest.with_suffix(dest.suffix + f".part{index}")
            if part_path.exists() and part_path.stat().st_size == end - start + 1:
                done[index] = part_path.stat().st_size
                return None
            expected = end - start + 1
            for attempt in range(5):
                try:
                    with httpx.Client(timeout=180, follow_redirects=True) as client:
                        with client.stream("GET", url,
                                           headers={"Range": f"bytes={start}-{end}"}) as r:
                            if r.status_code != 206:
                                return (f"part {index}: server ignored Range "
                                        f"(HTTP {r.status_code}); concatenating "
                                        f"this would corrupt the file")
                            got = 0
                            with part_path.open("wb") as fh:
                                for block in r.iter_bytes(1 << 18):
                                    fh.write(block)
                                    got += len(block)
                                    done[index] = got
                    if got == expected:
                        return None
                    last = f"part {index}: got {got} bytes, expected {expected}"
                except Exception as exc:
                    last = f"part {index}: {exc}"
                time.sleep(2)
            return last

        failures = []
        with ThreadPoolExecutor(max_workers=len(parts)) as pool:
            futures = [pool.submit(one, p) for p in parts]
            while not all(f.done() for f in futures):
                time.sleep(5)
                got = sum(done)
                rate = got / 1e6 / max(1e-9, time.perf_counter() - started)
                print(f"  {got/1e6:>7.1f} / {total/1e6:.1f} MB  ({rate:.2f} MB/s)",
                      flush=True)
            for f in as_completed(futures):
                err = f.result()
                if err:
                    failures.append(err)

        if failures:
            print("\nDownload failed:")
            for f in failures:
                print(f"  {f}")
            print("Re-run to retry; completed parts are kept.")
            return 1

        with dest.open("wb") as out:
            for index, _, _ in parts:
                part_path = dest.with_suffix(dest.suffix + f".part{index}")
                out.write(part_path.read_bytes())
        for index, _, _ in parts:
            dest.with_suffix(dest.suffix + f".part{index}").unlink(missing_ok=True)

        elapsed = time.perf_counter() - started
        print(f"  assembled in {elapsed/60:.1f} min "
              f"({total/1e6/elapsed:.2f} MB/s average)")
        return verify(dest, total)
    finally:
        lock.unlink(missing_ok=True)


def verify(dest: Path, expected: int) -> int:
    size = dest.stat().st_size
    if size != expected:
        print(f"  FAILED: {size} bytes on disk, {expected} declared.")
        return 1
    if dest.suffix == ".zip":
        if not zipfile.is_zipfile(dest):
            print("  FAILED: right size, but not a readable zip.")
            return 1
        print("  verified: readable zip of the declared size")
    else:
        print("  verified: size matches the declared length")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    return fetch(args.url, args.out, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
