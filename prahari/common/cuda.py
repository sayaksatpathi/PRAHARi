"""Making CUDA discoverable to ONNX Runtime on Windows.

ONNX Runtime's CUDA execution provider does not ship the CUDA runtime. It
dynamically loads `cudart64_12.dll`, `cublas64_12.dll` and `cudnn*64_9.dll` at
session creation, and on Windows those have to be on the DLL search path - which,
since Python 3.8, is *not* `PATH` for extension modules. Miss them and you get
the least helpful failure in the stack:

    LoadLibrary failed with error 126 when trying to load
    onnxruntime_providers_cuda.dll
    -> Failed to create CUDAExecutionProvider

followed by a session that quietly runs on the CPU. Nothing raises. The only
symptom is that inference is ten times slower than it should be, which is easy
to mistake for "the GPU is just not very fast", and that is exactly the sort of
silent downgrade this project refuses to ship.

So the resolution is explicit and reported:

*   `cuda_dll_dir` in settings (or `PRAHARI_CUDA_DLL_DIR`) names a directory to
    register, for a deployment with a CUDA Toolkit install in a known place;
*   failing that, `discover()` looks in the usual places, including any CUDA
    libraries an already-installed package happens to carry - on a workstation
    that has PyTorch, its `torch/lib` holds a complete, matching CUDA 12 +
    cuDNN 9 set, and borrowing those DLLs does not make torch a dependency of
    the node (nothing imports it; only the directory is read);
*   whatever is chosen is logged, and `describe()` reports it, so "is this
    actually on the GPU?" always has an answer that is not a guess.

None of this runs on an edge node without a GPU. `prepare()` is a no-op off
Windows and when no CUDA directory is found, and the caller falls back to the
CPU provider exactly as before.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("prahari.cuda")

# The libraries ORT's CUDA EP needs to find. cuDNN 9 and CUDA 12 for ORT 1.20.
_REQUIRED = ("cudart64_12.dll", "cublas64_12.dll", "cudnn64_9.dll")

_state: dict[str, object] = {"prepared": False, "dir": None, "reason": ""}


def _has_cuda_runtime(directory: Path) -> bool:
    return directory.is_dir() and all((directory / name).exists()
                                      for name in _REQUIRED)


def _candidate_dirs() -> list[Path]:
    """Places a matching CUDA 12 + cuDNN 9 runtime plausibly lives."""
    candidates: list[Path] = []

    # An installed CUDA Toolkit, newest first.
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    toolkit = Path(program_files) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if toolkit.is_dir():
        candidates += sorted((p / "bin" for p in toolkit.iterdir() if p.is_dir()),
                             reverse=True)

    # nvidia-* pip packages, which place DLLs under site-packages/nvidia/*/bin.
    for entry in sys.path:
        nvidia = Path(entry) / "nvidia"
        if nvidia.is_dir():
            candidates += [p / "bin" for p in nvidia.iterdir() if p.is_dir()]

    # A PyTorch CUDA build carries a complete matching set in torch/lib. This is
    # opportunistic, not a dependency: the directory is located from sys.path
    # without importing torch, so a node without it simply finds nothing here.
    for entry in sys.path:
        lib = Path(entry) / "torch" / "lib"
        if lib.is_dir():
            candidates.append(lib)

    return candidates


def discover(configured: str | os.PathLike | None = None) -> Path | None:
    """The first directory holding a usable CUDA runtime, or None."""
    if configured:
        directory = Path(configured)
        if _has_cuda_runtime(directory):
            return directory
        log.warning("cuda_dll_dir %s does not contain %s; ignoring it",
                    directory, ", ".join(_REQUIRED))
    for directory in _candidate_dirs():
        if _has_cuda_runtime(directory):
            return directory
    return None


def prepare(configured: str | os.PathLike | None = None) -> bool:
    """Put a CUDA runtime on the DLL search path. Idempotent.

    Returns True when ONNX Runtime should now be able to create a CUDA session.
    Never raises: a failure here means CPU inference, which is a degraded mode,
    not a broken one.
    """
    if _state["prepared"]:
        return _state["dir"] is not None
    _state["prepared"] = True

    if not sys.platform.startswith("win"):
        _state["reason"] = "not Windows; the loader finds CUDA without help"
        return True

    directory = discover(configured)
    if directory is None:
        _state["reason"] = (
            "no CUDA 12 + cuDNN 9 runtime found. Set PRAHARI_CUDA_DLL_DIR to a "
            "directory containing " + ", ".join(_REQUIRED))
        log.info("CUDA: %s", _state["reason"])
        return False

    try:
        os.add_dll_directory(str(directory))
    except OSError as exc:
        _state["reason"] = f"could not register {directory}: {exc}"
        log.warning("CUDA: %s", _state["reason"])
        return False

    # Both are needed, and it is worth writing down why, because getting only
    # the first one right looks like success and is not.
    #
    # `add_dll_directory` covers DLLs the *loader* resolves for us. But ORT
    # opens `onnxruntime_providers_cuda.dll` itself, by absolute path, through
    # plain LoadLibrary - and the dependencies of a DLL opened that way are
    # resolved by the default search order, which includes PATH and does *not*
    # include directories added by `add_dll_directory`. The provider DLL is
    # found, its imports are not, and the result is a bare "error 126" followed
    # by a silent fall back to the CPU.
    path = os.environ.get("PATH", "")
    entry = str(directory)
    if entry not in path.split(os.pathsep):
        os.environ["PATH"] = entry + os.pathsep + path

    _state["dir"] = directory
    _state["reason"] = f"CUDA runtime taken from {directory}"
    log.info("CUDA: %s", _state["reason"])
    return True


def describe() -> dict[str, object]:
    """What was resolved, for the status API and the benchmark reports."""
    return {
        "prepared": bool(_state["prepared"]),
        "dll_dir": str(_state["dir"]) if _state["dir"] else None,
        "detail": _state["reason"],
    }


def providers_for(device: str, configured: str | os.PathLike | None = None
                  ) -> list[str]:
    """The ONNX Runtime provider list for a requested device.

    CUDA is only offered when the runtime was actually made loadable, so a
    session never silently falls back after advertising the GPU.
    """
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if prepare(configured):
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if device == "cuda":
        log.warning("CUDA was requested but %s", _state["reason"])
    return ["CPUExecutionProvider"]
