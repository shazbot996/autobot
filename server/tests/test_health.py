"""`autobot_server.health` and `gpu`: what `/health` reports, and the VRAM budget (IS-10, IS-11)."""

from __future__ import annotations

import pytest

from autobot_server.contract import GpuMemory, Health, contract_version
from autobot_server.gpu import (
    TOTAL_MIB,
    GpuError,
    budget,
    check_budget,
    parse_memory,
)
from autobot_server.health import health
from autobot_server.job import Job, NotReady
from tests.server_fakes import FakeEngine, FakeGpu, health_jobs


def test_the_budget_is_that_of_is_10() -> None:
    plan = budget()
    assert (
        plan.stt_mib,
        plan.llm_weights_mib,
        plan.llm_kv_cache_mib,
        plan.cuda_contexts_mib,
    ) == (3072, 5120, 1536, 1024)
    assert plan.total_mib == TOTAL_MIB == 10752


def test_the_budget_fits_the_2080_ti() -> None:
    check_budget(FakeGpu(used_mib=300, free_mib=10964))


def test_a_card_too_small_for_the_budget_stops_the_server_in_one_line() -> None:
    with pytest.raises(
        GpuError, match="budget of 10752 MiB exceeds the card's 8192 MiB"
    ):
        check_budget(FakeGpu(used_mib=300, free_mib=7892))


def test_nvidia_smis_figures_are_read() -> None:
    assert parse_memory("7650, 3360\n") == GpuMemory(used_mib=7650, free_mib=3360)


@pytest.mark.parametrize("output", ["", "N/A, N/A", "7650"])
def test_nvidia_smi_without_figures_is_one_line(output: str) -> None:
    with pytest.raises(GpuError, match="nvidia-smi gave no memory figures"):
        parse_memory(output)


def test_health_reports_every_job_and_the_card() -> None:
    answer = health(health_jobs(), FakeGpu(), contract_version())
    assert answer.contract_version == "1"
    assert answer.tts is False
    assert answer.jobs.stt.ready and answer.jobs.stt.model == "large-v3-turbo"
    assert answer.gpu == GpuMemory(used_mib=7650, free_mib=3360)
    assert answer.budget == budget()
    assert Health.from_json(answer.to_json()) == answer


def test_a_job_not_ready_says_why_and_its_model_is_not_touched() -> None:
    engine = FakeEngine("qwen3:8b")
    engine.load_error = NotReady("model qwen3:8b is not in Ollama")
    chat: Job[object, object] = Job("chat", engine)
    chat.start()
    jobs = {**health_jobs(), "chat": chat}
    before = (engine.loads, engine.warm_ups, len(engine.runs))
    answer = health(jobs, FakeGpu(), "1")
    assert (engine.loads, engine.warm_ups, len(engine.runs)) == before
    assert not answer.jobs.chat.ready
    assert answer.jobs.chat.reason == "model qwen3:8b is not in Ollama"
    assert answer.jobs.chat.model == "qwen3:8b"


def test_health_answers_when_the_card_cannot_be_read() -> None:
    answer = health(health_jobs(), FakeGpu(broken=True), "1")
    assert answer.gpu == GpuMemory(used_mib=0, free_mib=0)
