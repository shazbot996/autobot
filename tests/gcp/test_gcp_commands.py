"""`autobot.gcp.commands`: the gcloud commands, filled in, and the scripts."""

import shlex
import subprocess
from pathlib import Path

import pytest

from autobot.config import GEMINI_DEVELOPER_API, GcpConfig
from autobot.gcp import commands, needs
from autobot.gcp.commands import Command

CONFIG = GcpConfig()
PROJECT = "schiele-autobot-dev"
ACCOUNT = "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"
WEATHER = needs.EXPECTED_SECRETS[0]
GCP_SOURCE = Path(commands.__file__).parent


def every_command(config: GcpConfig = CONFIG) -> list[Command]:
    """Every command the screen can show for a configuration."""
    apis = [api.name for api in needs.needed_apis(config)]
    return [
        *commands.project_commands(config),
        commands.describe_project(config),
        commands.enable_apis(config, apis),
        commands.list_enabled_apis(config),
        *commands.service_account_commands(config),
        commands.describe_service_account(config),
        *commands.remove_broad_roles(config, needs.TOO_BROAD),
        commands.list_account_roles(config),
        *commands.key_commands(config),
        commands.list_keys(config),
        *commands.secret_commands(config, WEATHER),
        commands.describe_secret(config, WEATHER.name),
        commands.create_api_key(config, WEATHER),
        commands.restrict_api_key(
            config, WEATHER, "projects/1/locations/global/keys/k"
        ),
        commands.list_api_keys(config, WEATHER),
        commands.cap_quota(config, needs.WEATHER_API, "RequestsPerDayPerProject", 100),
        commands.cap_quota(
            config, "aiplatform.googleapis.com", "PerMinutePerRegion", 10
        ),
        commands.list_quotas(config, needs.WEATHER_API),
        commands.describe_quota(config, needs.WEATHER_API, "RequestsPerDayPerProject"),
        *commands.bucket_commands(config, "autobot-state"),
        commands.describe_bucket(config, "autobot-state"),
    ]


def test_every_command_is_one_line_with_a_purpose() -> None:
    for command in every_command():
        assert command.text and "\n" not in command.text
        assert command.purpose and "\n" not in command.purpose
        # It parses as a shell command line.
        assert shlex.split(command.text)


def test_the_commands_are_filled_in_from_the_configuration() -> None:
    needs_project = [
        command
        for command in every_command()
        if command.text.startswith("gcloud")
        and " auth " not in command.text
        and "gs://" not in command.text
        and "api-keys update" not in command.text
    ]
    assert len(needs_project) > 20
    for command in needs_project:
        assert PROJECT in command.text, command.text
    for command in (
        *commands.service_account_commands(CONFIG)[1:],
        *commands.secret_commands(CONFIG, WEATHER)[1:],
        *commands.remove_broad_roles(CONFIG, needs.TOO_BROAD),
        *commands.bucket_commands(CONFIG, "autobot-state")[1:],
    ):
        assert f"--member=serviceAccount:{ACCOUNT}" in command.text
    assert "--locations=us-east5" in commands.create_secret(CONFIG, WEATHER).text
    assert "--location=us-east5" in commands.create_bucket(CONFIG, "b").text
    regional = commands.cap_quota(CONFIG, "aiplatform.googleapis.com", "XPerRegion", 10)
    assert "--dimensions=region=us-east5" in regional.text
    assert "--preferred-value=10" in regional.text
    daily = commands.cap_quota(
        CONFIG, needs.WEATHER_API, "RequestsPerDayPerProject", 100
    )
    assert "--dimensions" not in daily.text
    assert "--quota-id=RequestsPerDayPerProject" in daily.text


def test_another_configuration_gives_other_commands() -> None:
    config = GcpConfig(
        project="other-project",
        region="europe-west1",
        service_account="bot@other-project.iam.gserviceaccount.com",
        credentials_file="secrets/bot.json",
        gemini_api=GEMINI_DEVELOPER_API,
        buckets=("state",),
    )
    text = "\n".join(command.text for command in every_command(config))
    assert PROJECT not in text and "us-east5" not in text and ACCOUNT not in text
    assert "gcloud iam service-accounts create bot --project=other-project" in text
    enable = commands.enable_apis(
        config, [api.name for api in needs.needed_apis(config)]
    )
    assert "generativelanguage.googleapis.com" in enable.text
    assert "storage.googleapis.com" in enable.text
    assert "aiplatform.googleapis.com" not in enable.text
    # Vertex AI is not the chosen API, so its role is not granted.
    assert "roles/aiplatform.user" not in text


def test_values_are_shell_quoted() -> None:
    odd = WEATHER.__class__(
        name="a secret; rm -rf ~", agent="x", holds="x", api_key="k", api_target="t"
    )
    words = shlex.split(commands.create_secret(CONFIG, odd).text)
    assert words[:4] == ["gcloud", "secrets", "create", "a secret; rm -rf ~"]


def test_the_secret_roles_are_granted_on_the_secret_not_the_project() -> None:
    grants = commands.secret_commands(CONFIG, WEATHER)[1:]
    assert [command.text.split("--role=")[1] for command in grants] == [
        "roles/secretmanager.viewer",
        "roles/secretmanager.secretAccessor",
        "roles/secretmanager.secretVersionAdder",
    ]
    for command in grants:
        assert command.text.startswith(
            f"gcloud secrets add-iam-policy-binding {WEATHER.name} "
        )
    project_grants = commands.service_account_commands(CONFIG)[1:]
    assert all("secretmanager" not in command.text for command in project_grants)
    assert all(
        command.text.startswith(f"gcloud projects add-iam-policy-binding {PROJECT} ")
        for command in project_grants
    )


def test_the_weather_key_is_restricted_to_the_weather_api() -> None:
    target = "--api-target=service=weather.googleapis.com"
    assert target in commands.create_api_key(CONFIG, WEATHER).text
    assert (
        "--display-name=autobot-weather"
        in commands.create_api_key(CONFIG, WEATHER).text
    )
    update = commands.restrict_api_key(
        CONFIG, WEATHER, "projects/1/locations/global/keys/k"
    )
    assert update.text == (
        f"gcloud services api-keys update projects/1/locations/global/keys/k {target}"
    )


def test_a_broad_permission_gives_the_removal_of_the_roles_that_grant_it() -> None:
    removals = commands.remove_broad_roles(CONFIG, ["serviceusage.services.enable"])
    assert [command.text.split("--role=")[1].split()[0] for command in removals] == [
        "roles/owner",
        "roles/editor",
        "roles/serviceusage.serviceUsageAdmin",
    ]
    assert all("remove-iam-policy-binding" in command.text for command in removals)
    # A role mapped to two permissions is removed once.
    both = commands.remove_broad_roles(CONFIG, list(needs.TOO_BROAD))
    assert len({command.text for command in both}) == len(both)


def test_the_key_script_creates_the_key_and_ends_in_chmod_600() -> None:
    script = commands.render_key_script(CONFIG)
    lines = script.rstrip("\n").splitlines()
    key = "secrets/service-account-autobot-agent.json"
    assert lines[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in lines
    assert "umask 077" in lines
    assert "mkdir -p secrets" in lines
    assert "chmod 700 secrets" in lines
    assert (
        f"gcloud iam service-accounts keys create {key} "
        f"--iam-account={ACCOUNT} --project={PROJECT}"
    ) in lines
    assert lines[-1] == f"chmod 600 {key}"
    # The key is never printed.
    for word in ("cat ", "echo ", "print", "base64"):
        assert word not in script


def test_write_script_writes_under_run_gcp(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = commands.write_script("key", commands.key_commands(CONFIG), run_dir)
    assert path == run_dir / "gcp" / "key.sh"
    assert path.read_text(encoding="utf-8") == commands.render_key_script(CONFIG)
    assert commands.script_name("key") == "run/gcp/key.sh"
    # Writing again replaces the file, and nothing else is written.
    commands.write_script("key", [Command("true", "Nothing")], run_dir)
    assert "gcloud" not in path.read_text(encoding="utf-8").split("set -euo")[1]
    assert sorted(item.name for item in run_dir.rglob("*")) == ["gcp", "key.sh"]


def test_a_script_says_what_each_command_is_for() -> None:
    script = commands.render_script("apis", [Command("gcloud services list", "List")])
    assert "\n# List\ngcloud services list\n" in script
    assert "autobot never runs this script" in script


@pytest.mark.parametrize(
    "source", sorted(GCP_SOURCE.glob("*.py")), ids=lambda p: p.name
)
def test_nothing_in_the_package_passes_gcloud_to_a_subprocess(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    for runner in ("os.system", "os.popen", "os.exec", "os.spawn", "pty.", "Popen"):
        assert runner not in text, f"{source.name} uses {runner}"
    if "subprocess" in text:
        # One module runs one command, `git check-ignore`, and builds no gcloud line.
        assert source.name == "keyfile.py"
        assert "gcloud" not in text


@pytest.mark.parametrize("width", [20, 40, 71, 79, 119])
def test_a_folded_command_pastes_as_the_one_command(width: int) -> None:
    for command in every_command():
        rows = commands.fold_command(command.text, width)
        assert all(len(row) <= width for row in rows), rows
        # A shell drops each backslash and the line break after it.
        assert all(row.endswith("\\") for row in rows[:-1])
        assert "".join(row[:-1] for row in rows[:-1]) + rows[-1] == command.text
        if len(command.text) <= width:
            assert rows == [command.text]


def test_a_command_is_never_folded_inside_quotes() -> None:
    text = "gcloud x --display-name='autobot agent with a long name' --project=p"
    for width in range(12, len(text)):
        rows = commands.fold_command(text, width)
        assert "".join(row[:-1] for row in rows[:-1]) + rows[-1] == text
        if width > 40:
            # The quoted value fits on a row, so no break falls inside it.
            assert all(row.count("'") % 2 == 0 for row in rows), rows


def test_bash_reads_a_folded_command_as_the_one_command() -> None:
    """`bash -n` only parses; the words are then compared, and nothing is run."""
    command = commands.list_account_roles(CONFIG)
    rows = commands.fold_command(command.text, 40)
    assert len(rows) > 2
    printed = subprocess.run(
        ["bash", "-c", "printf '%s\\n' \"$@\"", "bash", *shlex.split(command.text)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    folded = subprocess.run(
        [
            "bash",
            "-c",
            "set -- " + "\n".join(rows)[len("gcloud ") :] + "\nprintf '%s\\n' \"$@\"",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert folded == printed.split("\n", 1)[1]


def test_the_account_is_created_only_when_it_is_not_there() -> None:
    command = commands.create_service_account(CONFIG)
    assert command.text == (
        f"gcloud iam service-accounts describe {ACCOUNT} --project={PROJECT} "
        ">/dev/null 2>&1 || gcloud iam service-accounts create autobot-agent "
        f"--project={PROJECT} --display-name='autobot agent'"
    )
    # The key script creates the account before it makes the key.
    script = commands.render_key_script(CONFIG)
    assert script.index("service-accounts create") < script.index(" keys create ")
    assert (
        subprocess.run(["bash", "-n"], input=script, text=True, check=False).returncode
        == 0
    )
