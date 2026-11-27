"""`autobot.gcp.checks`: the eight steps and their states, against a fake Google Cloud."""

from pathlib import Path

import pytest

from autobot.config import GEMINI_DEVELOPER_API, GcpConfig, QuotaConfig
from autobot.gcp import needs
from autobot.gcp.checks import (
    NO_KEY_YET,
    STEP_ACCOUNT,
    STEP_APIS,
    STEP_GEMINI,
    STEP_KEY,
    STEP_ORDER,
    STEP_PROJECT,
    STEP_QUOTAS,
    STEP_SECRETS,
    STEP_STORAGE,
    State,
    Step,
    run_all,
)
from autobot.gcp.client import (
    PROJECT,
    UNLIMITED,
    ApiKey,
    GcpError,
    bucket_resource,
    secret_resource,
)
from gcp.gcp_fakes import KEY_NAME, FakeGoogleCloud, no_key, ready_cloud, ready_key

WEATHER_DAY = QuotaConfig("weather.googleapis.com", "RequestsPerDayPerProject", 100)
WEATHER_MINUTE = QuotaConfig(
    "weather.googleapis.com", "RequestsPerMinutePerProject", 10
)
GEMINI_MINUTE = QuotaConfig(
    "aiplatform.googleapis.com", "RequestsPerMinutePerRegion", 10
)
MODEL = "gemini-3.5-flash-lite"
CONFIG = GcpConfig(
    gemini_model=MODEL, quotas=(WEATHER_DAY, WEATHER_MINUTE, GEMINI_MINUTE)
)
REPO = Path("/repo")
SECRET = "autobot-weather-api-key"
ACCOUNT_DEPENDENT = (STEP_APIS, STEP_ACCOUNT, STEP_SECRETS, STEP_GEMINI, STEP_QUOTAS)


def check(
    cloud: FakeGoogleCloud | None, config: GcpConfig = CONFIG, refused: str = ""
) -> dict[str, Step]:
    key = ready_key(config, REPO) if cloud or refused else no_key(config, REPO)
    steps = run_all(config, cloud, key, refused)
    assert tuple(step.id for step in steps) == STEP_ORDER
    return {step.id: step for step in steps}


def texts(step: Step) -> list[str]:
    return [finding.text for finding in step.findings]


def lines(step: Step) -> list[str]:
    return [command.text for command in step.commands]


def test_the_steps_are_the_eight_of_the_spec_in_order() -> None:
    steps = run_all(CONFIG, None, no_key(CONFIG, REPO))
    assert [step.title for step in steps] == [
        "Project",
        "APIs",
        "Service account",
        "Key",
        "Secret Manager",
        "Gemini",
        "Rate limits",
        "Cloud Storage",
    ]
    assert all(step.purpose for step in steps)


def test_with_no_key_nothing_is_asked_and_every_account_step_is_unchecked() -> None:
    cloud = FakeGoogleCloud()
    steps = check(None)
    for name in ACCOUNT_DEPENDENT:
        step = steps[name]
        assert step.state is State.UNCHECKED
        assert step.label == f"unchecked: {NO_KEY_YET}"
        # It shows the commands that set the step up, and a read-only one.
        assert step.commands, name
        assert step.read_only, name
    assert cloud.calls == []
    assert steps[STEP_PROJECT].state is State.READY
    assert steps[STEP_KEY].state is State.MISSING
    assert steps[STEP_KEY].label == "missing"
    assert lines(steps[STEP_KEY])[-1].startswith("chmod 600 secrets/")
    assert steps[STEP_STORAGE].state is State.NOT_USED
    assert steps[STEP_STORAGE].label == "not used yet"
    # Before the key exists the owner is shown the whole setup of each step.
    assert len(lines(steps[STEP_ACCOUNT])) == 1 + len(needs.roles_on(needs.ON_PROJECT))
    assert (
        "|| gcloud iam service-accounts create autobot-agent "
        in (lines(steps[STEP_ACCOUNT])[0])
    )
    # The owner may start at the Key step: its commands create the account
    # first, before the key is made for it.
    key = lines(steps[STEP_KEY])
    create = next(i for i, line in enumerate(key) if "service-accounts create" in line)
    make_key = next(i for i, line in enumerate(key) if " keys create " in line)
    assert create < make_key
    assert key[create] == lines(steps[STEP_ACCOUNT])[0]
    assert any("A key needs its account" in text for text in texts(steps[STEP_KEY]))
    assert "gcloud secrets create" in lines(steps[STEP_SECRETS])[0]
    assert [status.exists for status in steps[STEP_SECRETS].secrets] == [False]


def test_a_step_says_which_earlier_step_it_waits_on() -> None:
    steps = check(None)
    assert steps[STEP_PROJECT].waits_on == ""
    assert steps[STEP_APIS].waits_on == ""  # Project is ready.
    assert steps[STEP_ACCOUNT].waits_on == "APIs"
    assert steps[STEP_KEY].waits_on == "Service account"
    for name in (STEP_SECRETS, STEP_GEMINI, STEP_QUOTAS, STEP_STORAGE):
        assert steps[name].waits_on == "Key"


def test_with_everything_in_place_every_step_is_ready() -> None:
    cloud = ready_cloud(CONFIG)
    steps = check(cloud)
    for name in (STEP_PROJECT, STEP_KEY, *ACCOUNT_DEPENDENT):
        step = steps[name]
        assert step.state is State.READY, (name, texts(step))
        assert step.commands == () or name == STEP_PROJECT
        assert step.waits_on == ""
    assert steps[STEP_STORAGE].state is State.NOT_USED
    # Cloud Storage is not used, so nothing was asked about it.
    assert cloud.made("bucket_exists") == []
    assert SECRET in " ".join(texts(steps[STEP_SECRETS]))
    status = steps[STEP_SECRETS].secrets[0]
    assert (status.name, status.agent) == (SECRET, "weather")
    assert status.exists and status.can_add and status.has_version
    # The secret's value was never asked for: the seam has no call that reads it.
    assert {call[0] for call in cloud.calls} == {
        "enabled_services",
        "test_permissions",
        "secret_versions",
        "api_keys",
        "quota_limit",
        "gemini_models",
    }
    # Regional quotas are compared in the configured region.
    assert all(call[3] == "us-east5" for call in cloud.made("quota_limit"))


def test_one_disabled_api_is_missing_with_that_one_enable_line() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.enabled.discard("weather.googleapis.com")
    step = check(cloud)[STEP_APIS]
    assert step.state is State.MISSING
    assert lines(step) == [
        "gcloud services enable weather.googleapis.com --project=schiele-autobot-dev"
    ]
    assert (
        "missing  weather.googleapis.com: the weather agent's lookup (AG-6)"
        in texts(step)
    )
    assert sum(text.startswith("enabled  ") for text in texts(step)) == 7


def test_a_missing_permission_names_the_role_that_grants_it() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.permissions[PROJECT].discard("apikeys.keys.list")
    step = check(cloud)[STEP_ACCOUNT]
    assert step.state is State.MISSING
    assert (
        "missing  apikeys.keys.list: granted by roles/serviceusage.apiKeysViewer"
        in texts(step)
    )
    assert lines(step) == [
        (
            "gcloud projects add-iam-policy-binding schiele-autobot-dev "
            "--member=serviceAccount:autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com "
            "--role=roles/serviceusage.apiKeysViewer --condition=None"
        )
    ]


def test_a_broad_permission_is_too_broad_with_the_removal_commands() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.permissions[PROJECT].add("serviceusage.services.enable")
    step = check(cloud)[STEP_ACCOUNT]
    assert step.state is State.MISSING
    assert any(
        text.startswith("too broad: holds serviceusage.services.enable")
        for text in texts(step)
    )
    assert [line.split("--role=")[1].split()[0] for line in lines(step)] == [
        "roles/owner",
        "roles/editor",
        "roles/serviceusage.serviceUsageAdmin",
    ]
    assert all("remove-iam-policy-binding" in line for line in lines(step))
    # The owner sees which role it is with the read-only policy command.
    assert any("get-iam-policy" in command.text for command in step.read_only)


def test_a_weather_quota_above_the_cap_is_missing_with_the_command_for_the_cap() -> (
    None
):
    cloud = ready_cloud(CONFIG)
    cloud.quotas[(WEATHER_DAY.api, WEATHER_DAY.metric)] = 1000
    step = check(cloud)[STEP_QUOTAS]
    assert step.state is State.MISSING
    assert (
        "missing  weather.googleapis.com RequestsPerDayPerProject is 1000, "
        "above the intended 100" in texts(step)
    )
    assert len(lines(step)) == 1
    assert "--quota-id=RequestsPerDayPerProject --preferred-value=100" in lines(step)[0]
    # The other two caps are in place.
    assert sum(text.startswith("ready    ") for text in texts(step)) == 2


def test_a_quota_with_no_cap_or_no_such_id_is_missing() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.quotas[(WEATHER_MINUTE.api, WEATHER_MINUTE.metric)] = UNLIMITED
    del cloud.quotas[(GEMINI_MINUTE.api, GEMINI_MINUTE.metric)]
    step = check(cloud)[STEP_QUOTAS]
    assert step.state is State.MISSING
    found = "\n".join(texts(step))
    assert "RequestsPerMinutePerProject has no cap; intended 10" in found
    assert "aiplatform.googleapis.com has no quota with the id" in found
    # An id that does not exist gets the command that lists the real ones.
    assert any(
        "quotas info list --service=aiplatform.googleapis.com" in command.text
        for command in step.read_only
    )
    assert len(lines(step)) == 1


def test_a_billed_api_with_no_entry_is_noted_and_does_not_fail_the_step() -> None:
    config = GcpConfig(quotas=(WEATHER_DAY, WEATHER_MINUTE))
    step = check(ready_cloud(config), config)[STEP_QUOTAS]
    assert step.state is State.READY
    noted = [f for f in step.findings if f.text.startswith("no cap   aiplatform")]
    assert [finding.role for finding in noted] == ["warning"]
    # The command that lists its quota ids is there for when it is capped.
    assert any(
        "quotas info list --service=aiplatform.googleapis.com" in command.text
        for command in step.read_only
    )


def test_a_configured_bucket_that_does_not_exist_is_missing() -> None:
    config = GcpConfig(quotas=CONFIG.quotas, buckets=("autobot-state",))
    cloud = ready_cloud(config)
    assert check(cloud, config)[STEP_STORAGE].state is State.READY
    assert "storage.googleapis.com" in cloud.enabled
    cloud.buckets.clear()
    step = check(cloud, config)[STEP_STORAGE]
    assert step.state is State.MISSING
    assert "missing  the bucket autobot-state does not exist" in texts(step)
    assert lines(step)[0].startswith(
        "gcloud storage buckets create gs://autobot-state "
    )


def test_a_bucket_the_account_cannot_write_to_is_missing() -> None:
    config = GcpConfig(quotas=CONFIG.quotas, buckets=("autobot-state",))
    cloud = ready_cloud(config)
    cloud.permissions[bucket_resource("autobot-state")].discard(
        "storage.objects.create"
    )
    step = check(cloud, config)[STEP_STORAGE]
    assert step.state is State.MISSING
    assert len(lines(step)) == 1
    assert "--role=roles/storage.objectCreator" in lines(step)[0]


def test_a_secret_that_does_not_exist_gets_its_create_and_grant_commands() -> None:
    cloud = ready_cloud(CONFIG)
    del cloud.secrets[SECRET]
    del cloud.permissions[secret_resource(SECRET)]
    step = check(cloud)[STEP_SECRETS]
    assert step.state is State.MISSING
    assert lines(step)[0].startswith(f"gcloud secrets create {SECRET} ")
    assert sum("secrets add-iam-policy-binding" in line for line in lines(step)) == 3
    status = step.secrets[0]
    assert not status.exists and not status.can_add and not status.has_version
    # Its versions were not asked for: the account cannot list them.
    assert cloud.made("secret_versions") == []


def test_a_secret_with_no_enabled_version_is_missing_and_can_be_pasted() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.secrets[SECRET] = ["DESTROYED"]
    step = check(cloud)[STEP_SECRETS]
    assert step.state is State.MISSING
    assert any("has no value yet" in text for text in texts(step))
    # The key exists, so the step shows how its value gets into the secret:
    # through a pipe in the owner's shell, or printed for the masked field.
    assert lines(step) == [
        (
            f"gcloud services api-keys get-key-string {KEY_NAME} "
            "--format='value(keyString)' | tr -d '\\n' | gcloud secrets versions add "
            f"{SECRET} --project=schiele-autobot-dev --data-file=-"
        ),
        f"gcloud services api-keys get-key-string {KEY_NAME} --format='value(keyString)'",
    ]
    status = step.secrets[0]
    assert status.exists and status.can_add and not status.has_version


def test_an_unrestricted_weather_key_gets_the_restriction_command() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.keys = [ApiKey(KEY_NAME, "autobot-weather", ())]
    step = check(cloud)[STEP_SECRETS]
    assert step.state is State.MISSING
    assert any("can call any API" in text for text in texts(step))
    assert lines(step) == [
        (
            f"gcloud services api-keys update {KEY_NAME} "
            "--api-target=service=weather.googleapis.com"
        )
    ]
    cloud.keys = []
    step = check(cloud)[STEP_SECRETS]
    assert "missing  no API key named autobot-weather" in texts(step)
    assert "api-keys create" in lines(step)[0]


def test_gemini_on_vertex_needs_the_api_and_the_permission() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.permissions[PROJECT].discard("aiplatform.endpoints.predict")
    step = check(cloud)[STEP_GEMINI]
    assert step.state is State.MISSING
    assert "Vertex AI (aiplatform.googleapis.com) in global" in texts(step)[0]
    assert len(lines(step)) == 1 and "--role=roles/aiplatform.user" in lines(step)[0]


def test_the_developer_api_changes_what_is_needed() -> None:
    config = GcpConfig(
        gemini_api=GEMINI_DEVELOPER_API,
        gemini_model=MODEL,
        quotas=(WEATHER_DAY, QuotaConfig(GEMINI_DEVELOPER_API, "PerMinute", 10)),
    )
    cloud = ready_cloud(config)
    steps = check(cloud, config)
    assert steps[STEP_GEMINI].state is State.READY
    assert "Gemini Developer API" in texts(steps[STEP_GEMINI])[0]
    assert steps[STEP_QUOTAS].state is State.READY
    assert steps[STEP_ACCOUNT].state is State.READY
    assert any(GEMINI_DEVELOPER_API in text for text in texts(steps[STEP_APIS]))
    assert not any("aiplatform" in text for text in texts(steps[STEP_APIS]))


def test_a_failed_call_makes_only_its_own_step_unchecked() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.errors["quota_limit"] = GcpError(
        "cloudquotas.googleapis.com answered 403: Permission denied"
    )
    steps = check(cloud)
    quotas = steps[STEP_QUOTAS]
    assert quotas.state is State.UNCHECKED
    assert quotas.label == (
        "unchecked: cloudquotas.googleapis.com answered 403: Permission denied"
    )
    assert "\n" not in quotas.label
    # A denial points at the account's roles, which is where it is fixed.
    assert any(
        "run the grants on the Service account step" in text for text in texts(quotas)
    )
    assert not any("run the grants" in text for text in texts(check(None)[STEP_APIS]))
    for name in (STEP_APIS, STEP_ACCOUNT, STEP_SECRETS, STEP_GEMINI):
        assert steps[name].state is State.READY


def test_a_call_several_steps_share_is_made_once_and_fails_each_of_them() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.errors["enabled_services"] = GcpError(
        "cannot reach serviceusage.googleapis.com"
    )
    steps = check(cloud)
    for name in (STEP_APIS, STEP_SECRETS, STEP_GEMINI):
        assert steps[name].state is State.UNCHECKED
        assert steps[name].reason == "cannot reach serviceusage.googleapis.com"
    assert steps[STEP_ACCOUNT].state is State.READY
    assert steps[STEP_QUOTAS].state is State.READY
    assert len(cloud.made("enabled_services")) == 1


def test_a_refused_identity_is_shown_on_the_key_step_and_nothing_is_asked() -> None:
    line = (
        "refused: credentials resolve to other@x.iam.gserviceaccount.com, not autobot"
    )
    steps = check(None, refused=line)
    assert steps[STEP_KEY].state is State.MISSING
    assert line in texts(steps[STEP_KEY])
    for name in ACCOUNT_DEPENDENT:
        assert steps[name].label == f"unchecked: {line}"


@pytest.mark.parametrize("project", ["another-project"])
def test_an_account_of_another_project_makes_the_project_step_missing(
    project: str,
) -> None:
    config = GcpConfig(project=project, quotas=CONFIG.quotas)
    step = check(None, config)[STEP_PROJECT]
    assert step.state is State.MISSING
    assert any("is not an account of another-project" in text for text in texts(step))
    assert [command.text for command in step.commands] == [
        "gcloud config set project another-project",
        "gcloud auth login",
        "gcloud auth application-default login",
    ]


def test_w_writes_the_fixes_or_the_read_only_commands_when_there_are_none() -> None:
    steps = check(ready_cloud(CONFIG))
    assert steps[STEP_APIS].script == steps[STEP_APIS].read_only
    assert steps[STEP_PROJECT].script == steps[STEP_PROJECT].commands
    assert steps[STEP_STORAGE].script == ()


VERTEX = "GenerateContentRequestsPerMinutePerProjectPerRegionPerBaseModel"


def test_a_per_model_quota_without_a_model_asks_for_one_and_nothing_is_asked() -> None:
    config = GcpConfig(
        quotas=(WEATHER_DAY, QuotaConfig("aiplatform.googleapis.com", VERTEX, 10))
    )
    cloud = ready_cloud(config)
    step = check(cloud, config)[STEP_QUOTAS]
    assert step.state is State.MISSING
    assert any(
        "is counted per model: choose the model on the Gemini step" in text
        for text in texts(step)
    )
    # No command could set it, and its limit was not asked for.
    assert lines(step) == []
    assert [call[2] for call in cloud.made("quota_limit")] == [WEATHER_DAY.metric]
    # Before the key exists, the setup script leaves it out too.
    assert not any(VERTEX in line for line in lines(check(None, config)[STEP_QUOTAS]))


def test_a_per_model_quota_is_capped_for_its_model_in_the_region() -> None:
    entry = QuotaConfig("aiplatform.googleapis.com", VERTEX, 10, "gemini-2.5-flash")
    config = GcpConfig(quotas=(WEATHER_DAY, entry))
    cloud = ready_cloud(config)
    assert check(cloud, config)[STEP_QUOTAS].state is State.READY
    assert cloud.made("quota_limit")[-1] == (
        "quota_limit",
        "aiplatform.googleapis.com",
        VERTEX,
        "us-east5",
        "gemini-2.5-flash",
    )
    cloud.quotas[(entry.api, entry.metric)] = 600
    step = check(cloud, config)[STEP_QUOTAS]
    assert any("for gemini-2.5-flash is 600, above" in text for text in texts(step))
    assert len(lines(step)) == 1
    assert "--dimensions=region=us-east5,base_model=gemini-2.5-flash" in lines(step)[0]
    assert "--preferred-value=10" in lines(step)[0]


def test_a_per_model_cap_follows_the_chosen_gemini_model() -> None:
    entry = QuotaConfig("aiplatform.googleapis.com", VERTEX, 10)
    config = GcpConfig(gemini_model=MODEL, quotas=(WEATHER_DAY, entry))
    cloud = ready_cloud(config)
    cloud.quotas[(entry.api, entry.metric)] = 600
    step = check(cloud, config)[STEP_QUOTAS]
    # The entry names no model, so the cap is for the model the agent calls.
    assert cloud.made("quota_limit")[-1][4] == MODEL
    assert any(f"for {MODEL} is 600, above" in text for text in texts(step))
    assert f"--dimensions=region=us-east5,base_model={MODEL}" in lines(step)[0]
    # Before the key exists the setup script caps that model too.
    assert any(
        f"base_model={MODEL}" in line
        for line in lines(check(None, config)[STEP_QUOTAS])
    )


def test_the_gemini_step_lists_the_models_the_api_offers() -> None:
    cloud = ready_cloud(CONFIG)
    step = check(cloud)[STEP_GEMINI]
    assert step.state is State.READY
    assert step.models == tuple(cloud.models)
    assert MODEL in step.models and step.models == tuple(
        sorted(step.models, reverse=True)
    )
    assert f"ready    {MODEL} is a model the API offers" in texts(step)


def test_a_model_the_api_no_longer_offers_is_missing() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.models.remove(MODEL)
    step = check(cloud)[STEP_GEMINI]
    assert step.state is State.MISSING
    assert any(
        f"{MODEL} is not a model the API offers now" in text for text in texts(step)
    )


def test_no_chosen_model_is_missing() -> None:
    config = GcpConfig(quotas=(WEATHER_DAY, WEATHER_MINUTE))
    step = check(ready_cloud(config), config)[STEP_GEMINI]
    assert step.state is State.MISSING
    assert any("no model is chosen" in text for text in texts(step))


def test_models_that_cannot_be_listed_do_not_fail_the_step() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.errors["gemini_models"] = GcpError(
        "aiplatform.googleapis.com answered 403: no"
    )
    step = check(cloud)[STEP_GEMINI]
    assert step.state is State.READY
    assert step.models == ()
    assert any("The models could not be listed" in text for text in texts(step))
    assert f"Model    {MODEL} (not checked against the API)" in texts(step)
