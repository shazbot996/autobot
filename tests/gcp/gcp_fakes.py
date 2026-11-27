"""A fake Google Cloud for the tests: no key, no network.

`FakeGoogleCloud` is the `GoogleCloud` seam held in memory. `ready_cloud`
builds one in which everything a configuration needs is in place, so a test
takes one thing away and looks at what the checks make of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autobot.config import GcpConfig
from autobot.gcp import needs
from autobot.gcp.client import (
    PROJECT,
    ApiKey,
    GcpError,
    bucket_resource,
    secret_resource,
)
from autobot.gcp.keyfile import IGNORED, KeyState

# The Gemini models the fake API offers, besides the configured one.
OFFERED = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-pro")
KEY_NAME = "projects/1234/locations/global/keys/weather-key"


@dataclass
class FakeGoogleCloud:
    enabled: set[str] = field(default_factory=set)
    # The permissions the account holds, by resource (`client.PROJECT`,
    # `secret_resource(name)`, `bucket_resource(name)`).
    permissions: dict[str, set[str]] = field(default_factory=dict)
    # Each secret's versions, as their states.
    secrets: dict[str, list[str]] = field(default_factory=dict)
    # What `add_secret_version` was given, by secret.
    stored: dict[str, list[bytes]] = field(default_factory=dict)
    # A secret's latest value, set by a test; looked up before `stored`.
    values: dict[str, bytes] = field(default_factory=dict)
    # Every value `secret_value` handed out, so a test can see the caller
    # cleared it.
    given: list[bytearray] = field(default_factory=list)
    keys: list[ApiKey] = field(default_factory=list)
    # The live limit of each (api, quota id).
    quotas: dict[tuple[str, str], int] = field(default_factory=dict)
    buckets: set[str] = field(default_factory=set)
    # The Gemini models the API offers, newest first.
    models: list[str] = field(default_factory=list)
    # Every call made, as (method, argument...). Never a secret's value.
    calls: list[tuple[str, ...]] = field(default_factory=list)
    # A method named here raises its error when called.
    errors: dict[str, GcpError] = field(default_factory=dict)
    # When set, every call is refused with this line before it is recorded,
    # as a connection whose identity is not the service account's.
    refuse: str = ""

    def _called(self, method: str, *arguments: str) -> None:
        if self.refuse:
            raise GcpError(self.refuse)
        self.calls.append((method, *arguments))
        if method in self.errors:
            raise self.errors[method]

    def enabled_services(self) -> frozenset[str]:
        self._called("enabled_services")
        return frozenset(self.enabled)

    def test_permissions(
        self, resource: str, permissions: Sequence[str]
    ) -> frozenset[str]:
        self._called("test_permissions", resource)
        return frozenset(self.permissions.get(resource, set()) & set(permissions))

    def secret_versions(self, secret: str) -> tuple[str, ...]:
        self._called("secret_versions", secret)
        if secret not in self.secrets:
            raise GcpError(f"secretmanager.googleapis.com answered 403: no {secret}")
        return tuple(self.secrets[secret])

    def add_secret_version(self, secret: str, payload: bytearray) -> str:
        self._called("add_secret_version", secret)
        if secret not in self.secrets:
            raise GcpError(f"secretmanager.googleapis.com answered 404: no {secret}")
        self.stored.setdefault(secret, []).append(bytes(payload))
        self.secrets[secret].append("ENABLED")
        return f"projects/1234/secrets/{secret}/versions/{len(self.secrets[secret])}"

    def secret_value(self, secret: str) -> bytearray:
        self._called("secret_value", secret)
        latest = self.values.get(secret) or b"".join(self.stored.get(secret, [])[-1:])
        if not latest:
            raise GcpError(f"secret {secret} has no enabled version (HTTP 404)")
        value = bytearray(latest)
        self.given.append(value)
        return value

    def api_keys(self) -> tuple[ApiKey, ...]:
        self._called("api_keys")
        return tuple(self.keys)

    def quota_limit(
        self, api: str, metric: str, region: str, base_model: str = ""
    ) -> int | None:
        self._called("quota_limit", api, metric, region, base_model)
        return self.quotas.get((api, metric))

    def bucket_exists(self, bucket: str) -> bool:
        self._called("bucket_exists", bucket)
        return bucket in self.buckets

    def gemini_models(self) -> tuple[str, ...]:
        self._called("gemini_models")
        return tuple(self.models)

    def made(self, method: str) -> list[tuple[str, ...]]:
        """The calls of one method."""
        return [call for call in self.calls if call[0] == method]


def ready_cloud(config: GcpConfig) -> FakeGoogleCloud:
    """A Google Cloud in which everything `config` needs is in place."""
    cloud = FakeGoogleCloud()
    cloud.enabled = {api.name for api in needs.needed_apis(config)}
    cloud.permissions[PROJECT] = set(needs.permissions_on(needs.ON_PROJECT, config))
    for secret in needs.EXPECTED_SECRETS:
        cloud.permissions[secret_resource(secret.name)] = set(
            needs.permissions_on(needs.ON_SECRET)
        )
        cloud.secrets[secret.name] = ["ENABLED"]
        if secret.api_key:
            cloud.keys.append(ApiKey(KEY_NAME, secret.api_key, (secret.api_target,)))
    cloud.models = sorted({*OFFERED, config.gemini_model} - {""}, reverse=True)
    for quota in config.quotas:
        cloud.quotas[(quota.api, quota.metric)] = quota.limit
    for bucket in config.buckets:
        cloud.buckets.add(bucket)
        cloud.permissions[bucket_resource(bucket)] = set(
            needs.permissions_on(needs.ON_BUCKET)
        )
    return cloud


def ready_key(config: GcpConfig, repo: Path) -> KeyState:
    """A key state that reads *ready*, with no file behind it."""
    return KeyState(True, repo / config.credentials_file, config.credentials_file)


def no_key(config: GcpConfig, repo: Path) -> KeyState:
    return KeyState(
        False,
        repo / config.credentials_file,
        config.credentials_file,
        f"no key at {config.credentials_file} yet",
    )


def git_ignores(repo: Path, path: Path) -> int:
    """A fake `git check-ignore` that says every path is ignored."""
    return IGNORED


def write_key(config: GcpConfig, repo: Path, mode: int = 0o600) -> Path:
    """An empty stand-in for the key file, at the configured path. Not a key."""
    path = repo / config.credentials_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(mode)
    return path
