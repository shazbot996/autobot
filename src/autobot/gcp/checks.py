"""The eight steps of the Google Cloud screen, and the state of each.

`run_all` checks what is in place and returns one `Step` per step, in the
order the setup works in (GC-1): **Project**, **APIs**, **Service account**,
**Key**, **Secret Manager**, **Gemini**, **Rate limits**, **Cloud Storage**.
A step is *ready*, *missing*, *unchecked* (with the reason) or *not used
yet*, and carries what was found and the commands that fix what is missing.

Every question to Google Cloud goes through the `GoogleCloud` seam, as the
service account. With no connection (no key yet, or an identity that was
refused) nothing is asked: each step that needs the account reads
*unchecked* and shows the commands that set it up, for the owner to run with
his own login, which never passes through here (GC-4). A `GcpError` from a
call turns only the step that made it into *unchecked*.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from autobot.config import (
    GEMINI_VERTEX_AI,
    SERVICE_ACCOUNT_DOMAIN,
    GcpConfig,
)
from autobot.gcp import commands as cmd
from autobot.gcp import needs
from autobot.gcp.client import (
    PROJECT,
    UNLIMITED,
    ApiKey,
    GcpError,
    GoogleCloud,
    bucket_resource,
    secret_resource,
)
from autobot.gcp.commands import Command
from autobot.gcp.keyfile import KeyState

NO_KEY_YET = "no service account key yet"
# How a `GcpError` reads when Google Cloud denied the account the call.
DENIED = " answered 403:"

# Step identifiers: also the names of the scripts `W` writes.
STEP_PROJECT = "project"
STEP_APIS = "apis"
STEP_ACCOUNT = "service-account"
STEP_KEY = "key"
STEP_SECRETS = "secrets"
STEP_GEMINI = "gemini"
STEP_QUOTAS = "quotas"
STEP_STORAGE = "storage"

TITLES = {
    STEP_PROJECT: "Project",
    STEP_APIS: "APIs",
    STEP_ACCOUNT: "Service account",
    STEP_KEY: "Key",
    STEP_SECRETS: "Secret Manager",
    STEP_GEMINI: "Gemini",
    STEP_QUOTAS: "Rate limits",
    STEP_STORAGE: "Cloud Storage",
}
# The steps, in order.
STEP_ORDER = tuple(TITLES)

PURPOSES = {
    STEP_PROJECT: "The Google Cloud project and region every command and check uses.",
    STEP_APIS: "The APIs autobot's steps and agents call. You enable them: "
    "the service account is not allowed to.",
    STEP_ACCOUNT: "The dedicated account autobot runs as, with the narrowest "
    "roles that cover what it does and never a broad one.",
    STEP_KEY: "The account's key file: made by a script you run, git-ignored, "
    "readable by you alone. autobot never shows or copies it.",
    STEP_SECRETS: "The secrets autobot expects. A value you paste goes to "
    "Secret Manager and nowhere else.",
    STEP_GEMINI: "Which Gemini API the question agent calls, and whether the "
    "account can call it.",
    STEP_QUOTAS: "A cap in Google Cloud on every billed API an agent calls: "
    "the backstop behind the agent's own rate control.",
    STEP_STORAGE: "The buckets autobot is meant to use, and the account's "
    "access to them. Agent state and memory stay on the NUC.",
}

# The earlier step each step builds on (GC-1).
WAITS_ON = {
    STEP_APIS: STEP_PROJECT,
    STEP_ACCOUNT: STEP_APIS,
    STEP_KEY: STEP_ACCOUNT,
    STEP_SECRETS: STEP_KEY,
    STEP_GEMINI: STEP_KEY,
    STEP_QUOTAS: STEP_KEY,
    STEP_STORAGE: STEP_KEY,
}

# Finding roles: names of `Theme` fields the screen draws them in.
OK = "ok"
BAD = "error"
NOTE = "dim"
PLAIN = "normal"


class State(Enum):
    READY = "ready"
    MISSING = "missing"
    UNCHECKED = "unchecked"
    NOT_USED = "not used yet"


@dataclass(frozen=True)
class Finding:
    """One line of what a check found."""

    text: str
    role: str = PLAIN


@dataclass(frozen=True)
class SecretStatus:
    """An expected secret, as the Secret Manager step found it. Never its value."""

    name: str
    agent: str
    # The secret exists and the account can see it.
    exists: bool = False
    # The account may add a version: a pasted value can be stored.
    can_add: bool = False
    has_version: bool = False


@dataclass(frozen=True)
class Step:
    id: str
    state: State
    # Why the step is *unchecked*; empty otherwise.
    reason: str = ""
    findings: tuple[Finding, ...] = ()
    # The commands that fix what is missing, for the owner to run.
    commands: tuple[Command, ...] = ()
    # Read-only commands the owner can check the step with himself.
    read_only: tuple[Command, ...] = ()
    # The earlier step this one builds on, when that step is not *ready*.
    waits_on: str = ""
    # The Secret Manager step's secrets.
    secrets: tuple[SecretStatus, ...] = ()
    # The Gemini step's models: the ones the chosen API offers, newest first.
    # Empty when they could not be listed.
    models: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return TITLES[self.id]

    @property
    def purpose(self) -> str:
        return PURPOSES[self.id]

    @property
    def label(self) -> str:
        """The state as the screen shows it: `unchecked: no service account key yet`."""
        if self.state is State.UNCHECKED and self.reason:
            return f"{self.state.value}: {self.reason}"
        return self.state.value

    @property
    def script(self) -> tuple[Command, ...]:
        """What `W` writes: the fixes, or the read-only commands when there are none."""
        return self.commands or self.read_only


class _Probe:
    """Answers that several steps need, asked once.

    A failure is kept too and raised again in each step that asks, so every
    step that depends on the call reads *unchecked* with the same line.
    """

    def __init__(self, cloud: GoogleCloud) -> None:
        self.cloud = cloud
        self._enabled: frozenset[str] | GcpError | None = None
        self._keys: tuple[ApiKey, ...] | GcpError | None = None

    def enabled(self) -> frozenset[str]:
        if self._enabled is None:
            try:
                self._enabled = self.cloud.enabled_services()
            except GcpError as error:
                self._enabled = error
        if isinstance(self._enabled, GcpError):
            raise self._enabled
        return self._enabled

    def api_keys(self) -> tuple[ApiKey, ...]:
        if self._keys is None:
            try:
                self._keys = self.cloud.api_keys()
            except GcpError as error:
                self._keys = error
        if isinstance(self._keys, GcpError):
            raise self._keys
        return self._keys


def _state(findings: list[Finding]) -> State:
    return State.MISSING if any(f.role == BAD for f in findings) else State.READY


def _unique(commands: list[Command]) -> tuple[Command, ...]:
    return tuple(dict.fromkeys(commands))


# --- Project and Key: checked on this machine ---------------------------------


def _project(config: GcpConfig) -> Step:
    domain = f"@{config.project}{SERVICE_ACCOUNT_DOMAIN}"
    findings = [
        Finding(f"Project  {config.project}", OK),
        Finding(f"Region   {config.region}", OK),
    ]
    if config.service_account.endswith(domain):
        findings.append(Finding(f"Account  {config.service_account}", OK))
    else:
        findings.append(
            Finding(
                f"{config.service_account} is not an account of {config.project}: "
                "edit [gcp] service_account in the config file",
                BAD,
            )
        )
    return Step(
        STEP_PROJECT,
        _state(findings),
        findings=tuple(findings),
        commands=cmd.project_commands(config),
        read_only=(cmd.describe_project(config),),
    )


def _key(config: GcpConfig, key: KeyState, refused: str) -> Step:
    read_only = (cmd.list_keys(config),)
    if not key.ready:
        return Step(
            STEP_KEY,
            State.MISSING,
            findings=(
                Finding(key.reason, BAD),
                Finding(
                    "Run the commands below from the repository root, or press W "
                    "to have them written as a script.",
                    NOTE,
                ),
                Finding(
                    "A key needs its account: the commands create the service "
                    "account first when it does not exist yet. Its roles are "
                    "granted on the Service account step.",
                    NOTE,
                ),
            ),
            commands=cmd.key_commands(config),
            read_only=read_only,
        )
    ready = Finding(f"{key.shown}: exists, git-ignored, readable by you alone", OK)
    if refused:
        # The file is safe, and is not this account's key.
        return Step(
            STEP_KEY,
            State.MISSING,
            findings=(ready, Finding(refused, BAD)),
            commands=cmd.key_commands(config),
            read_only=read_only,
        )
    return Step(STEP_KEY, State.READY, findings=(ready,), read_only=read_only)


# --- what each account-dependent step shows before it can be checked -----------


def _setup(config: GcpConfig, step: str) -> tuple[Command, ...]:
    """Everything a step sets up: shown while the step cannot be checked."""
    if step == STEP_APIS:
        return (cmd.enable_apis(config, [a.name for a in needs.needed_apis(config)]),)
    if step == STEP_ACCOUNT:
        return cmd.service_account_commands(config)
    if step == STEP_SECRETS:
        found: list[Command] = []
        for secret in needs.EXPECTED_SECRETS:
            found += cmd.secret_commands(config, secret)
            if secret.api_key:
                found.append(cmd.create_api_key(config, secret))
        return tuple(found)
    if step == STEP_GEMINI:
        enable = cmd.enable_apis(config, [config.gemini_api])
        if config.gemini_api != GEMINI_VERTEX_AI:
            return (enable,)
        role = needs.role_granting(needs.VERTEX_PREDICT)
        assert role is not None
        return (enable, cmd.grant_project_role(config, role))
    if step == STEP_QUOTAS:
        return tuple(
            cmd.cap_quota(
                config,
                quota.api,
                quota.metric,
                quota.limit,
                cmd.quota_model(config, quota),
            )
            for quota in config.quotas
            if cmd.quota_model(config, quota) or not cmd.is_per_model(quota.metric)
        )
    if step == STEP_STORAGE:
        return tuple(
            command
            for bucket in config.buckets
            for command in cmd.bucket_commands(config, bucket)
        )
    return ()


def _look(config: GcpConfig, step: str) -> tuple[Command, ...]:
    """The read-only commands the owner can check a step with himself."""
    if step == STEP_APIS:
        return (cmd.list_enabled_apis(config),)
    if step == STEP_ACCOUNT:
        return (cmd.describe_service_account(config), cmd.list_account_roles(config))
    if step == STEP_SECRETS:
        found: list[Command] = []
        for secret in needs.EXPECTED_SECRETS:
            found.append(cmd.describe_secret(config, secret.name))
            if secret.api_key:
                found.append(cmd.list_api_keys(config, secret))
        return tuple(found)
    if step == STEP_GEMINI:
        return (cmd.list_enabled_apis(config), cmd.list_account_roles(config))
    if step == STEP_QUOTAS:
        apis = dict.fromkeys(
            [quota.api for quota in config.quotas] + list(needs.billed_apis(config))
        )
        return tuple(cmd.list_quotas(config, api) for api in apis)
    if step == STEP_STORAGE:
        return tuple(cmd.describe_bucket(config, bucket) for bucket in config.buckets)
    return ()


def _unchecked(config: GcpConfig, step: str, reason: str) -> Step:
    secrets = (
        tuple(SecretStatus(s.name, s.agent) for s in needs.EXPECTED_SECRETS)
        if step == STEP_SECRETS
        else ()
    )
    findings = [Finding(f"Not checked: {reason}.", NOTE)]
    if DENIED in reason:
        # Google Cloud refused the service account, not the owner: the usual
        # cause is that the account's roles are not granted yet.
        findings.append(
            Finding(
                "Google Cloud refused the service account. It most likely does "
                f"not hold its roles yet: run the grants on the {TITLES[STEP_ACCOUNT]} "
                "step, wait a minute, then press R.",
                "warning",
            )
        )
    findings.append(
        Finding(
            "The commands below set this step up; the last ones only look. "
            "They are for your own shell.",
            NOTE,
        )
    )
    return Step(
        step,
        State.UNCHECKED,
        reason=reason,
        findings=tuple(findings),
        commands=_setup(config, step),
        read_only=_look(config, step),
        secrets=secrets,
    )


# --- the checks that ask Google Cloud -----------------------------------------


def _apis(config: GcpConfig, probe: _Probe) -> Step:
    enabled = probe.enabled()
    findings: list[Finding] = []
    missing: list[str] = []
    for api in needs.needed_apis(config):
        if api.name in enabled:
            findings.append(Finding(f"enabled  {api.name}: {api.needed_for}", OK))
        else:
            missing.append(api.name)
            findings.append(Finding(f"missing  {api.name}: {api.needed_for}", BAD))
    return Step(
        STEP_APIS,
        _state(findings),
        findings=tuple(findings),
        commands=(cmd.enable_apis(config, missing),) if missing else (),
        read_only=_look(config, STEP_APIS),
    )


def _permission_findings(
    held: frozenset[str], wanted: tuple[str, ...], where: str = ""
) -> tuple[list[Finding], list[needs.Role]]:
    """A line per needed permission, and the roles that grant the missing ones."""
    findings: list[Finding] = []
    roles: list[needs.Role] = []
    for permission in wanted:
        if permission in held:
            findings.append(Finding(f"held     {permission}{where}", OK))
            continue
        role = needs.role_granting(permission)
        granted_by = f": granted by {role.name}" if role else ""
        findings.append(Finding(f"missing  {permission}{where}{granted_by}", BAD))
        if role and role not in roles:
            roles.append(role)
    return findings, roles


def _account(config: GcpConfig, probe: _Probe) -> Step:
    cloud = probe.cloud
    wanted = needs.permissions_on(needs.ON_PROJECT, config)
    held = cloud.test_permissions(PROJECT, wanted)
    findings, roles = _permission_findings(held, wanted)
    fixes = [cmd.grant_project_role(config, role) for role in roles]
    # Asked apart from the needed ones: a broad permission is one the account
    # must not hold, and one of them may not be testable on a project at all.
    broad = cloud.test_permissions(PROJECT, tuple(needs.TOO_BROAD))
    too_broad = [permission for permission in needs.TOO_BROAD if permission in broad]
    for permission in too_broad:
        granted_by = ", ".join(needs.TOO_BROAD[permission])
        findings.append(
            Finding(f"too broad: holds {permission} (granted by {granted_by})", BAD)
        )
    fixes += cmd.remove_broad_roles(config, too_broad)
    if too_broad:
        findings.append(
            Finding(
                "The last command below lists the account's roles: remove the "
                "broad one it shows.",
                NOTE,
            )
        )
    return Step(
        STEP_ACCOUNT,
        _state(findings),
        findings=tuple(findings),
        commands=_unique(fixes),
        read_only=_look(config, STEP_ACCOUNT),
    )


def _api_key(
    config: GcpConfig, secret: needs.ExpectedSecret, keys: tuple[ApiKey, ...]
) -> tuple[list[Finding], list[Command]]:
    """Whether the secret's API key is restricted to its one API: by name, never by value."""
    found = [key for key in keys if key.display_name == secret.api_key]
    if not found:
        return (
            [Finding(f"missing  no API key named {secret.api_key}", BAD)],
            [cmd.create_api_key(config, secret)],
        )
    findings: list[Finding] = []
    fixes: list[Command] = []
    for key in found:
        if key.api_targets == (secret.api_target,):
            findings.append(
                Finding(
                    f"ready    the key {secret.api_key} is restricted to "
                    f"{secret.api_target} only",
                    OK,
                )
            )
            continue
        now = ", ".join(key.api_targets) or "any API"
        findings.append(
            Finding(
                f"missing  the key {secret.api_key} can call {now}: restrict it "
                f"to {secret.api_target}",
                BAD,
            )
        )
        fixes.append(cmd.restrict_api_key(config, secret, key.name))
    return findings, fixes


def _secrets(config: GcpConfig, probe: _Probe) -> Step:
    cloud = probe.cloud
    findings: list[Finding] = []
    fixes: list[Command] = []
    statuses: list[SecretStatus] = []
    if needs.SECRET_MANAGER_API not in probe.enabled():
        findings.append(
            Finding(f"missing  {needs.SECRET_MANAGER_API} is not enabled", BAD)
        )
        fixes.append(cmd.enable_apis(config, [needs.SECRET_MANAGER_API]))
    wanted = needs.permissions_on(needs.ON_SECRET)
    for secret in needs.EXPECTED_SECRETS:
        held = cloud.test_permissions(secret_resource(secret.name), wanted)
        exists = needs.SECRET_VISIBLE in held
        has_version = False
        if not held:
            findings.append(
                Finding(
                    f"missing  {secret.name} ({secret.holds}): it does not exist, "
                    "or the account holds nothing on it",
                    BAD,
                )
            )
            fixes += cmd.secret_commands(config, secret)
        else:
            lines, roles = _permission_findings(held, wanted, f" on {secret.name}")
            findings += [line for line in lines if line.role == BAD]
            fixes += [cmd.grant_secret_role(config, secret.name, r) for r in roles]
            if needs.SECRET_LIST_VERSIONS in held:
                versions = cloud.secret_versions(secret.name)
                enabled = sum(1 for state in versions if state == "ENABLED")
                has_version = enabled > 0
                if has_version:
                    findings.append(
                        Finding(
                            f"ready    {secret.name} ({secret.holds}): "
                            f"{enabled} enabled version{'s' if enabled != 1 else ''}",
                            OK,
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            f"missing  {secret.name} ({secret.holds}) has no value "
                            "yet: run the first command below, or press Enter on "
                            "it to paste the value",
                            BAD,
                        )
                    )
        statuses.append(
            SecretStatus(
                secret.name,
                secret.agent,
                exists=exists,
                can_add=needs.SECRET_ADD_VERSION in held,
                has_version=has_version,
            )
        )
        if secret.api_key:
            keys = probe.api_keys()
            lines, more = _api_key(config, secret, keys)
            findings += lines
            fixes += more
            named = [key for key in keys if key.display_name == secret.api_key]
            if exists and not has_version and len(named) == 1:
                # The secret is empty and its key exists: how the key's
                # value gets into it.
                fixes.append(cmd.store_api_key(config, secret, named[0].name))
                fixes.append(cmd.print_api_key(secret, named[0].name))
    return Step(
        STEP_SECRETS,
        _state(findings),
        findings=tuple(findings),
        commands=_unique(fixes),
        read_only=_look(config, STEP_SECRETS),
        secrets=tuple(statuses),
    )


def _gemini(config: GcpConfig, probe: _Probe) -> Step:
    vertex = config.gemini_api == GEMINI_VERTEX_AI
    findings = [
        Finding(
            f"autobot calls Gemini on Vertex AI ({config.gemini_api}) in "
            f"{config.region}, as the service account"
            if vertex
            else f"autobot calls the Gemini Developer API ({config.gemini_api}), "
            "with an API key kept as a secret",
        )
    ]
    fixes: list[Command] = []
    if config.gemini_api in probe.enabled():
        findings.append(Finding(f"enabled  {config.gemini_api}", OK))
    else:
        findings.append(Finding(f"missing  {config.gemini_api} is not enabled", BAD))
        fixes.append(cmd.enable_apis(config, [config.gemini_api]))
    models: tuple[str, ...] = ()
    if vertex:
        wanted = (needs.VERTEX_PREDICT,)
        held = probe.cloud.test_permissions(PROJECT, wanted)
        lines, roles = _permission_findings(held, wanted)
        findings += lines
        fixes += [cmd.grant_project_role(config, role) for role in roles]
        try:
            models = probe.cloud.gemini_models()
        except GcpError as error:
            # The step still stands without the list: a model can be typed.
            findings.append(Finding(f"The models could not be listed: {error}", NOTE))
    else:
        findings.append(
            Finding(
                "Its API key is not yet a secret autobot expects: add it to "
                "autobot.gcp.needs when an agent calls this API.",
                NOTE,
            )
        )
    model = config.gemini_model
    if not model:
        findings.append(
            Finding("missing  no model is chosen: press Enter on Model to choose", BAD)
        )
    elif models and model not in models:
        findings.append(
            Finding(
                f"missing  {model} is not a model the API offers now: press "
                "Enter on Model to choose another",
                BAD,
            )
        )
    elif models:
        findings.append(Finding(f"ready    {model} is a model the API offers", OK))
    else:
        findings.append(Finding(f"Model    {model} (not checked against the API)"))
    return Step(
        STEP_GEMINI,
        _state(findings),
        findings=tuple(findings),
        commands=_unique(fixes),
        read_only=_look(config, STEP_GEMINI),
        models=models,
    )


def _quotas(config: GcpConfig, probe: _Probe) -> Step:
    findings: list[Finding] = []
    fixes: list[Command] = []
    look: list[Command] = []
    for quota in config.quotas:
        name = f"{quota.api} {quota.metric}"
        model = cmd.quota_model(config, quota)
        if model:
            name += f" for {model}"
        elif cmd.is_per_model(quota.metric):
            # One cap covers one model: without a model there is nothing to
            # compare the limit with, and no command that would set it.
            findings.append(
                Finding(
                    f"missing  {name} is counted per model: choose the model on "
                    f"the {TITLES[STEP_GEMINI]} step, or add base_model to its "
                    "[[gcp.quotas]] entry",
                    BAD,
                )
            )
            look.append(cmd.describe_quota(config, quota.api, quota.metric))
            continue
        live = probe.cloud.quota_limit(quota.api, quota.metric, config.region, model)
        if live is None:
            findings.append(
                Finding(
                    f"missing  {quota.api} has no quota with the id {quota.metric}: "
                    "list its ids with the command below, and edit [[gcp.quotas]]",
                    BAD,
                )
            )
            look.append(cmd.list_quotas(config, quota.api))
            continue
        look.append(cmd.describe_quota(config, quota.api, quota.metric))
        if live == UNLIMITED:
            findings.append(
                Finding(f"missing  {name} has no cap; intended {quota.limit}", BAD)
            )
        elif live > quota.limit:
            findings.append(
                Finding(
                    f"missing  {name} is {live}, above the intended {quota.limit}", BAD
                )
            )
        else:
            findings.append(
                Finding(
                    f"ready    {name} is {live} (intended at most {quota.limit})", OK
                )
            )
            continue
        fixes.append(cmd.cap_quota(config, quota.api, quota.metric, quota.limit, model))
    capped = {quota.api for quota in config.quotas}
    for api in needs.billed_apis(config):
        if api not in capped:
            # A note, not a failure: leaving a billed API uncapped for now
            # is the owner's to decide, and the step says that it is so.
            findings.append(
                Finding(
                    f"no cap   {api} is billed and has no [[gcp.quotas]] entry: "
                    "nothing in Google Cloud limits it",
                    "warning",
                )
            )
            look.append(cmd.list_quotas(config, api))
    return Step(
        STEP_QUOTAS,
        _state(findings),
        findings=tuple(findings),
        commands=_unique(fixes),
        read_only=_unique(look),
    )


NOT_USED_YET = (
    "[gcp] buckets is empty: nothing is kept in Cloud Storage. Agent state and "
    "memory stay on the NUC."
)


def _storage(config: GcpConfig, probe: _Probe) -> Step:
    cloud = probe.cloud
    findings: list[Finding] = []
    fixes: list[Command] = []
    if needs.STORAGE_API in probe.enabled():
        findings.append(Finding(f"enabled  {needs.STORAGE_API}", OK))
    else:
        findings.append(Finding(f"missing  {needs.STORAGE_API} is not enabled", BAD))
        fixes.append(cmd.enable_apis(config, [needs.STORAGE_API]))
    wanted = needs.permissions_on(needs.ON_BUCKET)
    for bucket in config.buckets:
        if not cloud.bucket_exists(bucket):
            findings.append(
                Finding(f"missing  the bucket {bucket} does not exist", BAD)
            )
            fixes += cmd.bucket_commands(config, bucket)
            continue
        findings.append(Finding(f"ready    the bucket {bucket} exists", OK))
        held = cloud.test_permissions(bucket_resource(bucket), wanted)
        lines, roles = _permission_findings(held, wanted, f" on {bucket}")
        findings += lines
        fixes += [cmd.grant_bucket_role(config, bucket, role) for role in roles]
    return Step(
        STEP_STORAGE,
        _state(findings),
        findings=tuple(findings),
        commands=_unique(fixes),
        read_only=_look(config, STEP_STORAGE),
    )


CHECKS: dict[str, Callable[[GcpConfig, _Probe], Step]] = {
    STEP_APIS: _apis,
    STEP_ACCOUNT: _account,
    STEP_SECRETS: _secrets,
    STEP_GEMINI: _gemini,
    STEP_QUOTAS: _quotas,
    STEP_STORAGE: _storage,
}


def run_all(
    config: GcpConfig,
    cloud: GoogleCloud | None,
    key: KeyState,
    refused: str = "",
) -> tuple[Step, ...]:
    """The eight steps, in order.

    `cloud` is the connection as the service account, or None when there is
    none: because the key is not *ready*, or because `connect` refused, in
    which case `refused` is its one line.
    """
    steps: dict[str, Step] = {
        STEP_PROJECT: _project(config),
        STEP_KEY: _key(config, key, refused if key.ready and cloud is None else ""),
    }
    probe = _Probe(cloud) if cloud is not None else None
    why = NO_KEY_YET if not key.ready else (refused or "not connected")
    for step, check in CHECKS.items():
        if step == STEP_STORAGE and not config.buckets:
            steps[step] = Step(
                step, State.NOT_USED, findings=(Finding(NOT_USED_YET, NOTE),)
            )
        elif probe is None:
            steps[step] = _unchecked(config, step, why)
        else:
            try:
                steps[step] = check(config, probe)
            except GcpError as error:
                steps[step] = _unchecked(config, step, str(error))
    ordered: list[Step] = []
    for step in STEP_ORDER:
        found = steps[step]
        earlier = WAITS_ON.get(step)
        if earlier and steps[earlier].state is not State.READY:
            found = Step(
                found.id,
                found.state,
                found.reason,
                found.findings,
                found.commands,
                found.read_only,
                TITLES[earlier],
                found.secrets,
                found.models,
            )
        ordered.append(found)
    return tuple(ordered)
