"""The answer to `GET /health`: what the server can do (IS-11).

It is built from the jobs' states and the card's memory, and never touches a
model: a job reports ready only once its model has loaded and answered a
warm-up (IS-23). Text-to-speech is reserved, so `tts` is false (IS-10).

When the card cannot be read, `/health` still answers, with the memory as
zero used and zero free; the jobs' reasons say what is wrong with them.
"""

from __future__ import annotations

from collections.abc import Mapping

from autobot_server import __version__
from autobot_server.contract import GpuMemory, Health, Jobs
from autobot_server.gpu import Gpu, GpuError, budget
from autobot_server.job import Readiness


def health(
    jobs: Mapping[str, Readiness],
    gpu: Gpu,
    contract_version: str,
    server_version: str = __version__,
) -> Health:
    """The `/health` body from `jobs`, keyed `stt`, `route` and `chat`, and `gpu`."""
    try:
        memory = gpu.memory()
    except GpuError:
        memory = GpuMemory(used_mib=0, free_mib=0)
    return Health(
        server_version=server_version,
        contract_version=contract_version,
        jobs=Jobs(
            stt=jobs["stt"].health(),
            route=jobs["route"].health(),
            chat=jobs["chat"].health(),
        ),
        tts=False,
        budget=budget(),
        gpu=memory,
    )
