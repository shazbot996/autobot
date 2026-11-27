"""The Google Cloud screen and the masked field, in the real `App` on fakes."""

import curses
from pathlib import Path

import pytest
from tui_fakes import (
    BACKSPACE,
    CONFIG_TEXT,
    DOWN,
    ENTER,
    ESC,
    F1,
    UP,
    Key,
    make_harness,
    typed,
)

from autobot.config import GcpConfig, QuotaConfig
from autobot.gcp.client import GcpError
from autobot.gcp.commands import fold_command, render_key_script
from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from gcp.gcp_fakes import ready_cloud

SECRET_VALUE = "AIza-s3cr3t-Token"
CLEAR = 21  # Ctrl-U empties a field.


def no_frame_shows(frames: list[str], *texts: str) -> bool:
    return not any(text in frame for frame in frames for text in texts)


# --- the masked field ---------------------------------------------------------


def test_a_secret_typed_into_the_masked_field_is_drawn_as_mask_characters(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [*typed(SECRET_VALUE), -1, ENTER])
    value = harness.app.prompt_secret("Paste the secret", "The weather API key:")
    assert value == bytearray(SECRET_VALUE.encode())
    screen = harness.screen
    assert screen.saw("Paste the secret")
    assert screen.saw("*" * len(SECRET_VALUE) + "_")
    assert screen.saw(f"{len(SECRET_VALUE)} characters, not shown")
    # No part of the value was ever on the screen: not even one typed letter
    # at a time.
    for cut in range(len(SECRET_VALUE) - 3):
        assert no_frame_shows(screen.frames, SECRET_VALUE[cut : cut + 4])
    # `T` typed a letter: the theme was not switched and nothing was written.
    assert harness.config_text() == CONFIG_TEXT


def test_escape_clears_the_buffer_and_returns_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [*typed("abc"), ESC])
    assert harness.app.prompt_secret("Paste", "Value:") is None


def test_backspace_and_ctrl_u_edit_the_masked_field(tmp_path: Path) -> None:
    keys: list[Key] = [*typed("abcd"), BACKSPACE, -1, CLEAR, -1, *typed("xy"), ENTER]
    harness = make_harness(tmp_path, keys)
    assert harness.app.prompt_secret("Paste", "Value:") == bytearray(b"xy")
    assert harness.screen.saw("***_")
    assert harness.screen.saw("0 characters, not shown")


def test_a_pasted_value_longer_than_the_field_stays_masked(tmp_path: Path) -> None:
    long = "k" * 200
    harness = make_harness(tmp_path, [*typed(long), -1, ENTER])
    assert harness.app.prompt_secret("Paste", "Value:") == bytearray(long.encode())
    assert harness.screen.saw("200 characters, not shown")
    assert no_frame_shows(harness.screen.frames, "kkkk")


def test_the_bytes_of_a_pasted_character_outside_ascii_are_kept(tmp_path: Path) -> None:
    pasted = "pä55".encode()
    harness = make_harness(tmp_path, [*pasted, ENTER])
    assert harness.app.prompt_secret("Paste", "Value:") == bytearray(pasted)


# --- the screen ---------------------------------------------------------------

# Up from the first entry wraps to Quit; once more is Google Cloud.
OPEN: list[Key] = [UP, UP, ENTER]
LEAVE: list[Key] = [ESC, "q"]
SECRET = "autobot-weather-api-key"
KEY_FILE = "secrets/service-account-autobot-agent.json"
ACCOUNT = "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"
QUOTAS = """
[gcp]
# The project autobot runs in.
project = "schiele-autobot-dev"
region = "us-east5"
gemini_model = "gemini-3.5-flash-lite"

[[gcp.quotas]]
api = "weather.googleapis.com"
metric = "RequestsPerDayPerProject"
limit = 100

[[gcp.quotas]]
api = "aiplatform.googleapis.com"
metric = "RequestsPerMinutePerRegion"
limit = 10
"""
CAPPED = GcpConfig(
    gemini_model="gemini-3.5-flash-lite",
    quotas=(
        QuotaConfig("weather.googleapis.com", "RequestsPerDayPerProject", 100),
        QuotaConfig("aiplatform.googleapis.com", "RequestsPerMinutePerRegion", 10),
    ),
)


def whole_rows(frames: list[str]) -> set[str]:
    """Every row any frame drew, as the mouse would select it: the whole row."""
    return {row for frame in frames for row in frame.splitlines()}


def files_under(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*") if path.is_file()]


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_menu_reaches_the_screen_and_the_eight_steps_are_drawn_in_order(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(tmp_path, [*OPEN, -1, *LEAVE], rows=rows, cols=cols)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("   Google Cloud")
    assert screen.saw("═ Google Cloud ")
    frame = next(frame for frame in screen.frames if "1  Project" in frame)
    labels = [
        "1  Project",
        "2  APIs",
        "3  Service account",
        "4  Key",
        "5  Secret Manager",
        "6  Gemini",
        "7  Rate limits",
        "8  Cloud Storage",
    ]
    positions = [frame.index(label) for label in labels]
    assert positions == sorted(positions)
    # A checkout with no key: nothing that needs the account was checked.
    for label in ("2  APIs", "3  Service account", "5  Secret Manager", "6  Gemini"):
        row = next(row for row in frame.splitlines() if label in row)
        assert "unchecked: no service account key yet" in row, row
    assert any("4  Key" in row and "missing" in row for row in frame.splitlines())
    assert any(
        "8  Cloud Storage" in row and "not used yet" in row
        for row in frame.splitlines()
    )
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    # Nothing was connected to, run or written.
    assert harness.connects == []
    assert harness.runner.calls == []
    assert harness.config_text() == CONFIG_TEXT
    assert not (tmp_path / "run").exists()


def test_with_no_key_the_key_step_shows_the_script_and_w_writes_it(
    tmp_path: Path,
) -> None:
    page, end = curses.KEY_NPAGE, curses.KEY_END
    keys: list[Key] = [*OPEN, "4", -1, page, -1, end, -1, "w", -1, ESC, *LEAVE]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Step 4 of 8: Key")
    assert screen.saw(f"no key at {KEY_FILE} yet")
    rows = whole_rows(screen.frames)
    # Each line of the script stands alone on its row, from the left edge.
    for line in (
        "umask 077",
        "mkdir -p secrets",
        "chmod 700 secrets",
        f"chmod 600 {KEY_FILE}",
    ):
        assert line in rows, line
    # The long one is continued with a backslash and pastes as one command.
    create = (
        f"gcloud iam service-accounts keys create {KEY_FILE} "
        f"--iam-account={ACCOUNT} --project=schiele-autobot-dev"
    )
    folded = fold_command(create, 79)
    assert len(folded) > 1
    for row in folded:
        assert row in rows, row
    script = tmp_path / "run" / "gcp" / "key.sh"
    assert script.read_text(encoding="utf-8") == render_key_script(GcpConfig())
    assert screen.saw("Written: run/gcp/key.sh. Read it, then run it yourself")
    # Written, and not run: the interface started no command at all.
    assert harness.runner.calls == []
    assert files_under(tmp_path / "run") == [script]


def test_typing_a_region_writes_it_to_the_config_file(tmp_path: Path) -> None:
    keys: list[Key] = [
        *OPEN,
        "1",
        DOWN,
        ENTER,
        CLEAR,
        *typed("east"),
        ENTER,
        -1,
        CLEAR,
        *typed("europe-west1"),
        ENTER,
        -1,
        ESC,
        *LEAVE,
    ]
    harness = make_harness(tmp_path, keys, config_text=CONFIG_TEXT + QUOTAS)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("► Region   us-east5")
    assert screen.saw("a region looks like us-east5")
    text = harness.config_text()
    assert 'region = "europe-west1"' in text
    assert 'project = "schiele-autobot-dev"' in text
    # The comments survive the write.
    assert "# The project autobot runs in." in text
    assert harness.app.config.gcp.region == "europe-west1"
    assert screen.saw("Saved: [gcp] region =")
    assert screen.saw("► Region   europe-west1")


def test_a_project_is_written_to_a_file_that_has_no_gcp_table_yet(
    tmp_path: Path,
) -> None:
    keys = [*OPEN, "1", ENTER, CLEAR, *typed("another-project"), ENTER, -1, ESC, *LEAVE]
    harness = make_harness(tmp_path, keys, cols=120)
    assert harness.run() == 0
    assert '[gcp]\nproject = "another-project"\n' in harness.config_text()
    # The account still belongs to the old project, and the step says so.
    assert harness.screen.saw("is not an account of another-project")


def test_with_everything_in_place_the_steps_read_ready(tmp_path: Path) -> None:
    cloud = ready_cloud(CAPPED)
    harness = make_harness(
        tmp_path,
        [*OPEN, -1, "r", -1, *LEAVE],
        config_text=CONFIG_TEXT + QUOTAS,
        cloud=cloud,
    )
    assert harness.run() == 0
    frame = [frame for frame in harness.screen.frames if "1  Project" in frame][-1]
    for label in (
        "1  Project",
        "2  APIs",
        "3  Service account",
        "4  Key",
        "5  Secret Manager",
        "6  Gemini",
        "7  Rate limits",
    ):
        row = next(row for row in frame.splitlines() if label in row)
        assert row.rstrip(" ║").endswith(" ready"), row
    assert any(
        "8  Cloud Storage" in row and "not used yet" in row
        for row in frame.splitlines()
    )
    # `R` checked everything again: a second connection, as the service account.
    assert harness.connects == [tmp_path, tmp_path]


def test_a_pasted_secret_goes_to_secret_manager_and_nowhere_else(
    tmp_path: Path,
) -> None:
    cloud = ready_cloud(CAPPED)
    keys = [*OPEN, "5", -1, ENTER, *typed(SECRET_VALUE), -1, ENTER, -1, ESC, *LEAVE]
    harness = make_harness(
        tmp_path, keys, config_text=CONFIG_TEXT + QUOTAS, cloud=cloud
    )
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Step 5 of 8: Secret Manager")
    assert screen.saw(f"► {SECRET}  (the weather agent): has a value")
    assert screen.saw(f"Paste {SECRET}")
    # One version was added, with the value, through the fake service account.
    assert cloud.made("add_secret_version") == [("add_secret_version", SECRET)]
    assert cloud.stored == {SECRET: [SECRET_VALUE.encode()]}
    assert screen.saw(f"Stored: {SECRET} has a new version in Secret Manager")
    # The value was on no frame...
    for cut in range(len(SECRET_VALUE) - 3):
        assert no_frame_shows(screen.frames, SECRET_VALUE[cut : cut + 4])
    # ...and is in no file under the temporary directory: not the config
    # file, not `run/`, not a log.
    for path in files_under(tmp_path):
        assert SECRET_VALUE.encode() not in path.read_bytes(), path
    assert harness.config_text() == CONFIG_TEXT + QUOTAS
    assert harness.runner.calls == []


def test_a_failed_store_is_one_line_and_the_value_is_not_in_it(tmp_path: Path) -> None:
    cloud = ready_cloud(CAPPED)
    cloud.errors["add_secret_version"] = GcpError(
        "secretmanager.googleapis.com answered 403:\n denied"
    )
    keys = [*OPEN, "5", ENTER, *typed(SECRET_VALUE), ENTER, -1, ESC, *LEAVE]
    harness = make_harness(
        tmp_path, keys, config_text=CONFIG_TEXT + QUOTAS, cloud=cloud, cols=120
    )
    assert harness.run() == 0
    screen = harness.screen
    line = (
        f"{SECRET} was not stored in schiele-autobot-dev: "
        "secretmanager.googleapis.com answered 403: denied"
    )
    assert screen.saw(line)
    assert cloud.stored == {}
    assert no_frame_shows(screen.frames, SECRET_VALUE, "Traceback")


def test_a_refused_identity_is_one_line_and_nothing_is_checked(tmp_path: Path) -> None:
    refusal = "refused: credentials resolve to owner@example.com, not autobot-agent"
    harness = make_harness(
        tmp_path, [*OPEN, -1, "4", -1, ESC, *LEAVE], connect_error=refusal, cols=120
    )
    assert harness.run() == 0
    screen = harness.screen
    assert any(
        "2  APIs" in row and f"unchecked: {refusal}" in row
        for row in whole_rows(screen.frames)
    )
    # The Key step says why the key is not used.
    assert screen.saw(f"{KEY_FILE}: exists, git-ignored, readable by you alone")
    assert screen.saw(f"  {refusal}")
    assert no_frame_shows(screen.frames, "Traceback")


def test_a_key_git_does_not_ignore_is_not_used(tmp_path: Path) -> None:
    cloud = ready_cloud(GcpConfig())
    harness = make_harness(
        tmp_path,
        [*OPEN, -1, "4", -1, ESC, *LEAVE],
        cloud=cloud,
        key_ignored=False,
        cols=120,
    )
    assert harness.run() == 0
    assert harness.screen.saw(f"git does not ignore {KEY_FILE}: the key is not used")
    assert harness.connects == []
    assert cloud.calls == []


def test_a_secret_that_does_not_exist_shows_its_commands_instead_of_a_field(
    tmp_path: Path,
) -> None:
    cloud = ready_cloud(CAPPED)
    del cloud.secrets[SECRET]
    del cloud.permissions[f"secrets/{SECRET}"]
    keys = [*OPEN, "5", ENTER, -1, curses.KEY_END, -1, ESC, *LEAVE]
    harness = make_harness(
        tmp_path, keys, config_text=CONFIG_TEXT + QUOTAS, cloud=cloud, rows=40, cols=120
    )
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw(f"{SECRET} cannot take a value yet")
    assert not screen.saw(f"Paste {SECRET}")
    rows = whole_rows(screen.frames)
    create = (
        f"gcloud secrets create {SECRET} --project=schiele-autobot-dev "
        "--replication-policy=user-managed --locations=us-east5"
    )
    for row in fold_command(create, 119):
        assert row in rows, row
    # Its three roles are granted on the secret itself.
    assert any(
        row.startswith(f"gcloud secrets add-iam-policy-binding {SECRET} ")
        for row in rows
    )
    granted = {row.split("--role=")[1] for row in rows if "--role=" in row}
    assert granted == {
        "roles/secretmanager.viewer",
        "roles/secretmanager.secretAccessor",
        "roles/secretmanager.secretVersionAdder",
    }
    assert cloud.stored == {}


def test_an_open_step_scrolls_and_escape_returns_to_the_steps(tmp_path: Path) -> None:
    keys: list[Key] = [
        *OPEN,
        "3",
        -1,
        curses.KEY_NPAGE,
        -1,
        curses.KEY_END,
        -1,
        curses.KEY_HOME,
        DOWN,
        UP,
        -1,
        ESC,
        -1,
        *LEAVE,
    ]
    harness = make_harness(tmp_path, keys, rows=MIN_HEIGHT, cols=MIN_WIDTH)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Step 3 of 8: Service account")
    assert screen.saw("rows 1-")
    # The read-only commands are at the end of the step.
    assert screen.saw("To look for yourself: these change nothing")
    # Escape closed the step, not the screen: the list is back, in its frame.
    assert "► 3  Service account" in screen.frames[-3]
    assert "═ Google Cloud " in screen.frames[-3]
    assert all(
        len(row) <= MIN_WIDTH for frame in screen.frames for row in frame.splitlines()
    )


def test_w_on_a_step_with_nothing_to_write_says_so(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [*OPEN, UP, "w", -1, *LEAVE])
    assert harness.run() == 0
    assert harness.screen.saw("Cloud Storage has no commands to write")
    assert not (tmp_path / "run").exists()


def test_f1_lists_the_screens_keys(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [*OPEN, F1, -1, ENTER, *LEAVE], rows=40)
    assert harness.run() == 0
    screen = harness.screen
    for text in (
        "R              check every step again",
        "W              write the step's",
    ):
        assert screen.saw(text), text


def test_the_gemini_model_is_picked_from_the_models_the_api_offers(
    tmp_path: Path,
) -> None:
    cloud = ready_cloud(CAPPED)
    # The list is newest first, and opens on the model that is chosen now.
    below = cloud.models[cloud.models.index("gemini-3.5-flash-lite") + 1]
    keys: list[Key] = [*OPEN, "6", -1, ENTER, -1, DOWN, ENTER, -1, ESC, *LEAVE]
    harness = make_harness(
        tmp_path, keys, config_text=CONFIG_TEXT + QUOTAS, cloud=cloud, rows=40, cols=120
    )
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("► Model    gemini-3.5-flash-lite")
    assert screen.saw(f"The {len(cloud.models)} Gemini models the API offers now")
    for model in cloud.models:
        assert screen.saw(model), model
    assert f'gemini_model = "{below}"' in harness.config_text()
    assert harness.app.config.gcp.gemini_model == below
    assert screen.saw(f"► Model    {below}")
    # Everything was checked again for the new model.
    assert len(harness.connects) == 2


def test_without_a_list_the_model_is_typed(tmp_path: Path) -> None:
    keys: list[Key] = [
        *OPEN,
        "6",
        ENTER,
        *typed("Not A Model"),
        ENTER,
        -1,
        CLEAR,
        *typed("gemini-3.8-flash"),
        ENTER,
        -1,
        ESC,
        *LEAVE,
    ]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw("► Model    (not chosen)")
    assert harness.screen.saw("a model id looks like gemini-2.5-flash")
    assert '[gcp]\ngemini_model = "gemini-3.8-flash"\n' in harness.config_text()
