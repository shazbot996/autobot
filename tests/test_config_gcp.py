"""`autobot.config`: the `[gcp]` table and its `[[gcp.quotas]]` entries."""

from typing import Any

import pytest

from autobot.config import (
    CONFIG_ENV_VAR,
    GEMINI_DEVELOPER_API,
    GEMINI_VERTEX_AI,
    ConfigError,
    GcpConfig,
    QuotaConfig,
    load_config,
    parse_config,
    project_complaint,
    region_complaint,
    repo_root,
)


def data(**gcp: Any) -> dict[str, Any]:
    return {
        "audio": {"input_device": "pipewire", "output_device": "pipewire"},
        "wake_phrases": [{"phrase": "autobot", "kind": "text_match", "target": "ack"}],
        "gcp": gcp,
    }


def test_the_committed_config_names_the_project_and_its_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    gcp = load_config().gcp
    assert gcp.project == "schiele-autobot-dev"
    assert gcp.region == "us-east5"
    assert gcp.service_account == (
        "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"
    )
    assert gcp.credentials_file == "secrets/service-account-autobot-agent.json"
    assert gcp.credentials_path == repo_root() / gcp.credentials_file
    assert gcp.gemini_api == GEMINI_VERTEX_AI
    assert gcp.gemini_model == "gemini-3.5-flash-lite"
    assert gcp.buckets == ()
    # Each of the Weather API's seven endpoints is capped per day and per
    # minute, and Gemini on Vertex AI per minute.
    weather = [quota for quota in gcp.quotas if quota.api == "weather.googleapis.com"]
    assert len(weather) == 14
    assert {quota.limit for quota in weather if quota.metric.endswith("PerDay")} == {
        100
    }
    assert {quota.limit for quota in weather if quota.metric.endswith("PerMinute")} == {
        10
    }
    assert sum(quota.metric.endswith("PerDay") for quota in weather) == 7
    assert "CurrentConditionsQueriesPerDay" in {quota.metric for quota in weather}
    # Gemini on Vertex AI is left uncapped for now.
    assert [quota for quota in gcp.quotas if quota not in weather] == []


def test_a_file_without_the_table_gets_the_defaults() -> None:
    without = data()
    del without["gcp"]
    assert parse_config(without).gcp == GcpConfig()
    assert parse_config(data()).gcp == GcpConfig()


def test_every_field_is_read() -> None:
    gcp = parse_config(
        data(
            project="another-project",
            region="europe-west1",
            service_account="bot@another-project.iam.gserviceaccount.com",
            credentials_file="secrets/bot.json",
            gemini_api=GEMINI_DEVELOPER_API,
            buckets=["one", "two"],
            quotas=[{"api": "a.googleapis.com", "metric": "PerDay", "limit": 5}],
        )
    ).gcp
    assert gcp == GcpConfig(
        project="another-project",
        region="europe-west1",
        service_account="bot@another-project.iam.gserviceaccount.com",
        credentials_file="secrets/bot.json",
        gemini_api=GEMINI_DEVELOPER_API,
        buckets=("one", "two"),
        quotas=(QuotaConfig("a.googleapis.com", "PerDay", 5),),
    )


@pytest.mark.parametrize(
    ("gcp", "message"),
    [
        ({"project": "Bad_Project"}, "[gcp] project = 'Bad_Project' is not valid"),
        ({"project": "abc"}, "[gcp] project = 'abc' is not valid"),
        ({"project": 7}, "[gcp] project must be a string"),
        ({"region": "east"}, "[gcp] region = 'east' is not valid"),
        ({"region": "US-EAST5"}, "[gcp] region = 'US-EAST5' is not valid"),
        (
            {"service_account": "owner@example.com"},
            "[gcp] service_account = 'owner@example.com' must be a service account",
        ),
        ({"credentials_file": " "}, "[gcp] credentials_file must not be empty"),
        (
            {"gemini_api": "discoveryengine.googleapis.com"},
            "[gcp] gemini_api = 'discoveryengine.googleapis.com' is not one of",
        ),
        ({"buckets": "one"}, "[gcp] buckets must be a list of bucket names"),
        ({"buckets": ["one", "one"]}, "[gcp] buckets lists 'one' more than once"),
        ({"quotas": [7]}, "[[gcp.quotas]] entry #1 must be a table"),
        (
            {"quotas": [{"api": "a", "metric": "m", "limit": 0}]},
            "[gcp.quotas #1] limit must be a positive whole number",
        ),
        (
            {"quotas": [{"api": "a", "metric": "m", "limit": 1.5}]},
            "[gcp.quotas #1] limit must be a positive whole number",
        ),
        (
            {"quotas": [{"api": "a", "metric": "m"}]},
            "[gcp.quotas #1] missing key: limit",
        ),
        (
            {"quotas": [{"api": "a", "limit": 1}]},
            "[gcp.quotas #1] missing key: metric",
        ),
        (
            {
                "quotas": [
                    {"api": "a", "metric": "m", "limit": 1},
                    {"api": "a", "metric": "m", "limit": 2},
                ]
            },
            "[[gcp.quotas]] a m is configured more than once",
        ),
        (
            {"quotas": [{"api": "a", "metric": "m", "limit": 1, "base_model": 7}]},
            "[gcp.quotas #1] base_model must be a string",
        ),
        (
            {"gemini_model": "Gemini 3.5 Flash"},
            "[gcp] gemini_model = 'Gemini 3.5 Flash' is not valid",
        ),
        ({"zone": "us-east5-a"}, "[gcp] unknown key: zone"),
    ],
)
def test_a_bad_value_is_one_line_naming_the_key(
    gcp: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError) as raised:
        parse_config(data(**gcp))
    text = str(raised.value)
    assert text.startswith(message), text
    assert "\n" not in text


def test_the_screen_and_the_file_agree_on_what_is_valid() -> None:
    assert project_complaint("schiele-autobot-dev") is None
    assert project_complaint("short") is not None
    assert project_complaint("ends-in-hyphen-") is not None
    assert region_complaint("us-east5") is None
    assert region_complaint("northamerica-northeast1") is None
    assert region_complaint("us-east5-a") is not None


def test_a_per_model_quota_names_its_model_and_one_id_may_cap_several() -> None:
    quotas = [
        {"api": "a", "metric": "PerBaseModel", "limit": 10, "base_model": "flash"},
        {"api": "a", "metric": "PerBaseModel", "limit": 5, "base_model": "pro"},
    ]
    gcp = parse_config(data(quotas=quotas)).gcp
    assert [quota.base_model for quota in gcp.quotas] == ["flash", "pro"]
    with pytest.raises(ConfigError):
        parse_config(data(quotas=[quotas[0], quotas[0]]))
