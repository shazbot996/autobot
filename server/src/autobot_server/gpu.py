"""The VRAM budget of IS-10 and the card's memory as `nvidia-smi` reads it.

The 2080 Ti has 11,264 MiB. Every resident model stays loaded (IS-24), so
what they may take is fixed here and checked at start: a budget that does not
fit the card stops the server with one line, rather than failing out of
memory mid-request. `/health` reports the budget and the memory used and
free (IS-11).

The card is read through the `Gpu` protocol, which the tests fake. Its one
implementation, `NvidiaSmi`, runs `nvidia-smi`, which the WSL driver
provides; no CUDA binding is imported (plan choice 3).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from autobot_server.contract import Budget, GpuMemory

# The budget of IS-10, in MiB.
STT_MIB = 3072
LLM_WEIGHTS_MIB = 5120
LLM_KV_CACHE_MIB = 1536
CUDA_CONTEXTS_MIB = 1024
TOTAL_MIB = STT_MIB + LLM_WEIGHTS_MIB + LLM_KV_CACHE_MIB + CUDA_CONTEXTS_MIB

NVIDIA_SMI_ARGUMENTS = (
    "--query-gpu=memory.used,memory.free",
    "--format=csv,noheader,nounits",
)
# Where the WSL driver puts `nvidia-smi`. A login shell has the directory on
# its PATH, but a systemd user unit does not, so it is looked for here when
# the PATH has no `nvidia-smi`.
WSL_NVIDIA_SMI = Path("/usr/lib/wsl/lib/nvidia-smi")
# How long `nvidia-smi` may take, in seconds.
NVIDIA_SMI_TIMEOUT = 5.0


class GpuError(Exception):
    """The card cannot be read, or the budget does not fit it. The message is one line."""


class Gpu(Protocol):
    """The card's memory, in MiB."""

    def memory(self) -> GpuMemory: ...


def budget() -> Budget:
    """The VRAM budget of IS-10."""
    return Budget(
        stt_mib=STT_MIB,
        llm_weights_mib=LLM_WEIGHTS_MIB,
        llm_kv_cache_mib=LLM_KV_CACHE_MIB,
        cuda_contexts_mib=CUDA_CONTEXTS_MIB,
        total_mib=TOTAL_MIB,
    )


def find_nvidia_smi(
    path: str | None = None, wsl_fallback: Path = WSL_NVIDIA_SMI
) -> str:
    """The `nvidia-smi` to run: from `path` (the PATH when `None`), else WSL's."""
    found = shutil.which("nvidia-smi", path=path)
    if found is not None:
        return found
    if wsl_fallback.is_file():
        return str(wsl_fallback)
    raise GpuError("nvidia-smi not found: the WSL GPU driver is not installed")


def check_budget(gpu: Gpu) -> None:
    """Raise `GpuError` when the budget exceeds the card's memory, used and free."""
    memory = gpu.memory()
    card = memory.used_mib + memory.free_mib
    if TOTAL_MIB > card:
        raise GpuError(
            f"the VRAM budget of {TOTAL_MIB} MiB exceeds the card's {card} MiB"
        )


class NvidiaSmi:
    """The first card's memory, read with `nvidia-smi`."""

    def memory(self) -> GpuMemory:
        command = (find_nvidia_smi(), *NVIDIA_SMI_ARGUMENTS)
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=NVIDIA_SMI_TIMEOUT,
                check=False,
            )
        except FileNotFoundError:
            raise GpuError(
                "nvidia-smi not found: the WSL GPU driver is not installed"
            ) from None
        except subprocess.TimeoutExpired:
            raise GpuError(
                f"nvidia-smi did not answer within {NVIDIA_SMI_TIMEOUT:g} s"
            ) from None
        if done.returncode != 0:
            last = (done.stderr or done.stdout).strip().splitlines()
            raise GpuError(
                f"nvidia-smi failed: {last[-1] if last else f'exit {done.returncode}'}"
            )
        return parse_memory(done.stdout)


def parse_memory(output: str) -> GpuMemory:
    """The first card's line of `nvidia-smi`'s CSV, as `used, free` in MiB."""
    lines = output.strip().splitlines()
    try:
        used, free = (int(part.strip()) for part in lines[0].split(","))
    except (IndexError, ValueError):
        raise GpuError(
            f"nvidia-smi gave no memory figures: {output.strip()!r}"
        ) from None
    return GpuMemory(used_mib=used, free_mib=free)
