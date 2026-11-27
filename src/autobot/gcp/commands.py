"""The `gcloud` commands that set autobot up in Google Cloud, as text.

Every change to the project is a command the owner runs himself, in his own
shell, with his own credentials (GC-2). This module only builds the text,
filled in from the configuration and shell-quoted, and writes it as a script
under `run/gcp/` when asked. Nothing in this package runs a command or a
script it builds.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autobot.config import GcpConfig, QuotaConfig
from autobot.gcp.needs import (
    ON_BUCKET,
    ON_PROJECT,
    ON_SECRET,
    TOO_BROAD,
    ExpectedSecret,
    Role,
    roles_on,
)

# Where `W` writes a step's commands, under `run/`.
SCRIPT_DIR = "gcp"


@dataclass(frozen=True)
class Command:
    """One shell command and what it is for."""

    text: str
    purpose: str


def _line(*words: str) -> str:
    return " ".join(words)


def _flag(name: str, value: str) -> str:
    return f"--{name}={shlex.quote(value)}"


def _project(config: GcpConfig) -> str:
    return _flag("project", config.project)


def _member(config: GcpConfig) -> str:
    return _flag("member", f"serviceAccount:{config.service_account}")


def account_id(config: GcpConfig) -> str:
    """The account's short name: what stands before the `@`."""
    return config.service_account.split("@", 1)[0]


# --- the Project step ---------------------------------------------------------


def project_commands(config: GcpConfig) -> tuple[Command, ...]:
    """The owner's own login, for his shell only: autobot never uses it (GC-3)."""
    return (
        Command(
            _line("gcloud", "config", "set", "project", shlex.quote(config.project)),
            "Point your own gcloud at the project",
        ),
        Command("gcloud auth login", "Refresh your own gcloud login"),
        Command(
            "gcloud auth application-default login",
            "Refresh your own Application Default Credentials",
        ),
    )


def describe_project(config: GcpConfig) -> Command:
    return Command(
        _line("gcloud", "projects", "describe", shlex.quote(config.project)),
        "See that the project exists and you can reach it",
    )


# --- the APIs step ------------------------------------------------------------


def enable_apis(config: GcpConfig, apis: Iterable[str]) -> Command:
    names = [shlex.quote(name) for name in apis]
    return Command(
        _line("gcloud", "services", "enable", *names, _project(config)),
        "Enable the APIs autobot needs" if len(names) != 1 else f"Enable {names[0]}",
    )


def list_enabled_apis(config: GcpConfig) -> Command:
    return Command(
        _line("gcloud", "services", "list", "--enabled", _project(config)),
        "List the APIs that are enabled",
    )


# --- the Service account step -------------------------------------------------


def create_service_account(config: GcpConfig) -> Command:
    """Create the account unless it is there already, so the line can be run again.

    The key cannot be made before the account exists, so the Key step shows
    this line too (`key_commands`).
    """
    exists = _line(
        "gcloud",
        "iam",
        "service-accounts",
        "describe",
        shlex.quote(config.service_account),
        _project(config),
    )
    create = _line(
        "gcloud",
        "iam",
        "service-accounts",
        "create",
        shlex.quote(account_id(config)),
        _project(config),
        _flag("display-name", "autobot agent"),
    )
    return Command(
        f"{exists} >/dev/null 2>&1 || {create}",
        "Create the dedicated service account, unless it exists already",
    )


def describe_service_account(config: GcpConfig) -> Command:
    return Command(
        _line(
            "gcloud",
            "iam",
            "service-accounts",
            "describe",
            shlex.quote(config.service_account),
            _project(config),
        ),
        "See that the service account exists",
    )


def grant_project_role(config: GcpConfig, role: Role) -> Command:
    return Command(
        _line(
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            shlex.quote(config.project),
            _member(config),
            _flag("role", role.name),
            "--condition=None",
        ),
        f"Let autobot {role.lets_autobot}",
    )


def remove_project_role(config: GcpConfig, role: str) -> Command:
    return Command(
        _line(
            "gcloud",
            "projects",
            "remove-iam-policy-binding",
            shlex.quote(config.project),
            _member(config),
            _flag("role", role),
            "--all",
        ),
        f"Take {role} away from the account: it is too broad",
    )


def remove_broad_roles(config: GcpConfig, held: Iterable[str]) -> tuple[Command, ...]:
    """The removal command for every role mapped to a too-broad permission held."""
    roles: list[str] = []
    for permission in held:
        for role in TOO_BROAD.get(permission, ()):
            if role not in roles:
                roles.append(role)
    return tuple(remove_project_role(config, role) for role in roles)


def list_account_roles(config: GcpConfig) -> Command:
    """Read-only: the roles the account holds on the project."""
    return Command(
        _line(
            "gcloud",
            "projects",
            "get-iam-policy",
            shlex.quote(config.project),
            _flag("flatten", "bindings[].members"),
            _flag(
                "filter", f"bindings.members:serviceAccount:{config.service_account}"
            ),
            _flag("format", "table(bindings.role)"),
        ),
        "List the roles the account holds on the project",
    )


def service_account_commands(config: GcpConfig) -> tuple[Command, ...]:
    """Create the account and grant every project role, one at a time."""
    return (
        create_service_account(config),
        *(grant_project_role(config, role) for role in roles_on(ON_PROJECT, config)),
    )


# --- the Key step -------------------------------------------------------------


def key_commands(config: GcpConfig) -> tuple[Command, ...]:
    """The lines of the key script (GC-7). The last one is the `chmod 600`.

    The account is created first when it does not exist yet: a key cannot be
    made for an account that is not there, and the owner may start at this
    step. The key is written straight to its place by `gcloud` and is never
    printed.
    A relative `credentials_file` is relative to the repository root, which is
    where the script runs (`render_script`).
    """
    key = PurePosixPath(config.credentials_file)
    folder = shlex.quote(str(key.parent))
    path = shlex.quote(str(key))
    return (
        Command("umask 077", "Nothing made here is readable by anyone else"),
        Command(f"mkdir -p {folder}", "The git-ignored directory the key is kept in"),
        Command(f"chmod 700 {folder}", "Only you may enter it"),
        create_service_account(config),
        Command(
            _line(
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "create",
                path,
                _flag("iam-account", config.service_account),
                _project(config),
            ),
            "Create the account's key, straight into its file",
        ),
        Command(f"chmod 600 {path}", "Only you may read the key"),
    )


def list_keys(config: GcpConfig) -> Command:
    return Command(
        _line(
            "gcloud",
            "iam",
            "service-accounts",
            "keys",
            "list",
            _flag("iam-account", config.service_account),
            _project(config),
        ),
        "List the account's keys",
    )


# --- the Secret Manager step --------------------------------------------------


def create_secret(config: GcpConfig, secret: ExpectedSecret) -> Command:
    return Command(
        _line(
            "gcloud",
            "secrets",
            "create",
            shlex.quote(secret.name),
            _project(config),
            "--replication-policy=user-managed",
            _flag("locations", config.region),
        ),
        f"Create the secret for {secret.holds}",
    )


def grant_secret_role(config: GcpConfig, secret: str, role: Role) -> Command:
    """Grant a role on the one secret, not on the project (GC-6)."""
    return Command(
        _line(
            "gcloud",
            "secrets",
            "add-iam-policy-binding",
            shlex.quote(secret),
            _project(config),
            _member(config),
            _flag("role", role.name),
        ),
        f"On this secret only, let autobot {role.lets_autobot}",
    )


def secret_commands(config: GcpConfig, secret: ExpectedSecret) -> tuple[Command, ...]:
    """Create one secret and grant its roles."""
    return (
        create_secret(config, secret),
        *(grant_secret_role(config, secret.name, role) for role in roles_on(ON_SECRET)),
    )


def describe_secret(config: GcpConfig, secret: str) -> Command:
    return Command(
        _line(
            "gcloud",
            "secrets",
            "versions",
            "list",
            shlex.quote(secret),
            _project(config),
        ),
        "See that the secret exists and has a version",
    )


def create_api_key(config: GcpConfig, secret: ExpectedSecret) -> Command:
    return Command(
        _line(
            "gcloud",
            "services",
            "api-keys",
            "create",
            _project(config),
            _flag("display-name", secret.api_key),
            _flag("api-target", f"service={secret.api_target}"),
        ),
        f"Create the API key, restricted to {secret.api_target} only",
    )


def restrict_api_key(config: GcpConfig, secret: ExpectedSecret, key: str) -> Command:
    """Restrict the key named `key` (its resource name) to the secret's one API."""
    return Command(
        _line(
            "gcloud",
            "services",
            "api-keys",
            "update",
            shlex.quote(key),
            _flag("api-target", f"service={secret.api_target}"),
        ),
        f"Restrict the key to {secret.api_target} only",
    )


def _key_string(key: str) -> str:
    return _line(
        "gcloud",
        "services",
        "api-keys",
        "get-key-string",
        shlex.quote(key),
        _flag("format", "value(keyString)"),
    )


def store_api_key(config: GcpConfig, secret: ExpectedSecret, key: str) -> Command:
    """Copy an API key's value into its secret, in the owner's shell.

    The value goes from one `gcloud` to the other through a pipe: it is never
    shown, and it never passes through autobot. `tr` drops the line break
    `gcloud` prints after it, which would otherwise become part of the secret.
    """
    add = _line(
        "gcloud",
        "secrets",
        "versions",
        "add",
        shlex.quote(secret.name),
        _project(config),
        "--data-file=-",
    )
    return Command(
        f"{_key_string(key)} | tr -d '\\n' | {add}",
        f"Copy the key's value straight into {secret.name}, without showing it",
    )


def print_api_key(secret: ExpectedSecret, key: str) -> Command:
    return Command(
        _key_string(key),
        "Or print the key's value, to paste it here with Enter",
    )


def list_api_keys(config: GcpConfig, secret: ExpectedSecret) -> Command:
    return Command(
        _line(
            "gcloud",
            "services",
            "api-keys",
            "list",
            _project(config),
            _flag("filter", f"displayName={secret.api_key}"),
        ),
        "See the API key and what it is restricted to",
    )


# --- the Rate limits step -----------------------------------------------------


def is_regional(quota_id: str) -> bool:
    """Whether a quota id names a per-region quota, compared in `[gcp] region`."""
    return "region" in quota_id.lower()


def is_per_model(quota_id: str) -> bool:
    """Whether a quota id names a quota counted per model: its cap names one."""
    return "basemodel" in quota_id.lower()


def quota_model(config: GcpConfig, quota: QuotaConfig) -> str:
    """The model a cap is for: the entry's own, else the chosen Gemini model.

    Empty for a quota that is not counted per model, and for a per-model one
    when no model is named anywhere.
    """
    if quota.base_model:
        return quota.base_model
    return config.gemini_model if is_per_model(quota.metric) else ""


def cap_quota(
    config: GcpConfig, api: str, quota_id: str, limit: int, base_model: str = ""
) -> Command:
    slug = re.sub(
        r"[^a-z0-9]+", "-", f"{api.split('.')[0]}-{quota_id}-{base_model}".lower()
    )
    words = [
        "gcloud",
        "beta",
        "quotas",
        "preferences",
        "create",
        _project(config),
        _flag("service", api),
        _flag("quota-id", quota_id),
        _flag("preferred-value", str(limit)),
        _flag("preference-id", f"autobot-{slug.strip('-')}"),
        "--allow-high-percentage-quota-decrease",
    ]
    dimensions = [f"region={config.region}"] if is_regional(quota_id) else []
    if base_model:
        dimensions.append(f"base_model={base_model}")
    if dimensions:
        words.append(_flag("dimensions", ",".join(dimensions)))
    model = f" for {base_model}" if base_model else ""
    return Command(_line(*words), f"Cap {api} {quota_id}{model} at {limit}")


def list_quotas(config: GcpConfig, api: str) -> Command:
    return Command(
        _line(
            "gcloud",
            "beta",
            "quotas",
            "info",
            "list",
            _flag("service", api),
            _project(config),
            _flag("format", "table(quotaId,refreshInterval,dimensions)"),
        ),
        f"List the quota ids of {api}",
    )


def describe_quota(config: GcpConfig, api: str, quota_id: str) -> Command:
    return Command(
        _line(
            "gcloud",
            "beta",
            "quotas",
            "info",
            "describe",
            shlex.quote(quota_id),
            _flag("service", api),
            _project(config),
        ),
        f"See the live limit of {api} {quota_id}",
    )


# --- the Cloud Storage step ---------------------------------------------------


def create_bucket(config: GcpConfig, bucket: str) -> Command:
    return Command(
        _line(
            "gcloud",
            "storage",
            "buckets",
            "create",
            shlex.quote(f"gs://{bucket}"),
            _project(config),
            _flag("location", config.region),
            "--uniform-bucket-level-access",
        ),
        f"Create the bucket {bucket}",
    )


def grant_bucket_role(config: GcpConfig, bucket: str, role: Role) -> Command:
    return Command(
        _line(
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            shlex.quote(f"gs://{bucket}"),
            _member(config),
            _flag("role", role.name),
        ),
        f"On this bucket only, let autobot {role.lets_autobot}",
    )


def bucket_commands(config: GcpConfig, bucket: str) -> tuple[Command, ...]:
    return (
        create_bucket(config, bucket),
        *(grant_bucket_role(config, bucket, role) for role in roles_on(ON_BUCKET)),
    )


def describe_bucket(config: GcpConfig, bucket: str) -> Command:
    return Command(
        _line(
            "gcloud", "storage", "buckets", "describe", shlex.quote(f"gs://{bucket}")
        ),
        f"See that the bucket {bucket} exists",
    )


# --- drawing a command so that it copies cleanly ------------------------------


def fold_command(text: str, width: int) -> list[str]:
    """`text` as rows of at most `width` characters that paste as one command.

    A row that is continued ends in a backslash, and the next row starts at
    the left edge with no indent, so the rows selected together with the mouse
    paste into a shell as the one command: the shell drops a backslash and
    the line break after it, even inside a word. A break is made at a space
    where one is in reach, and never inside single quotes, where a backslash
    would be taken literally.
    """
    if width < 8:
        return [text]
    rows: list[str] = []
    rest = text
    while len(rest) > width:
        quoted = False
        spaces: list[int] = []
        unquoted: list[int] = []
        # A row holds `width - 1` characters of the command and the backslash.
        for index, character in enumerate(rest[: width - 1]):
            if character == "'":
                quoted = not quoted
            elif not quoted:
                unquoted.append(index)
                if character == " ":
                    spaces.append(index)
        if spaces and spaces[-1] > 0:
            # Keep the space on this row: the next row starts with a word.
            cut = spaces[-1] + 1
        elif unquoted and unquoted[-1] > 0:
            cut = unquoted[-1] + 1
        else:
            cut = width - 1
        rows.append(rest[:cut] + "\\")
        rest = rest[cut:]
    rows.append(rest)
    return rows


# --- scripts ------------------------------------------------------------------


def render_script(step: str, commands: Sequence[Command]) -> str:
    """The text of `run/gcp/<step>.sh`: every command under a line saying what it is for.

    The script moves to the repository root first (it lives two directories
    below it), so a relative key path lands where the configuration says.
    """
    lines = [
        "#!/usr/bin/env bash",
        f"# autobot: the Google Cloud screen's commands for the step '{step}'.",
        "# Written by autobot for you to read and run:  bash " + script_name(step),
        "# autobot never runs this script. It runs with your own gcloud login.",
        "set -euo pipefail",
        'cd "$(dirname "${BASH_SOURCE[0]}")/../.."',
    ]
    for command in commands:
        lines += ["", f"# {command.purpose}", command.text]
    return "\n".join(lines) + "\n"


def render_key_script(config: GcpConfig) -> str:
    """The key script of GC-7. It ends in the `chmod 600` and never prints the key."""
    return render_script("key", key_commands(config))


def script_name(step: str) -> str:
    return f"run/{SCRIPT_DIR}/{step}.sh"


def write_script(step: str, commands: Sequence[Command], run_dir: Path) -> Path:
    """Write the commands of a step to `<run_dir>/gcp/<step>.sh`; return the path.

    The file is written and nothing more: running it is the owner's to do.
    """
    folder = run_dir / SCRIPT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{step}.sh"
    path.write_text(render_script(step, commands), encoding="utf-8")
    return path
