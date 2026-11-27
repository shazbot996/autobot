"""`autobot.wakeword.install`: install a candidate, keep the previous model, revert.

Everything happens under `tmp_path`, with made-up bytes for the model files:
installing moves files and edits the config file and never opens a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autobot.config import load_config
from autobot.tui.config_edit import Change, ConfigEditError, ConfigStore
from autobot.wakeword import install
from autobot.wakeword.install import InstallError

CONFIG_TEXT = """\
# autobot configuration: a comment at the top that must survive.

[host]
nuc_hostname = "nuc"

[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
# kind is the detector that implements the phrase.
kind = "text_match"
# Path of the model file, relative to the repository root.
model = ""
# Score from 0 to 1 at which the detector fires.
threshold = 0.5  # the fresh-install value
target = "ack"

[ack]
text = "Yes?"
voice = "en_US-joe-medium"

[tui]
# The colour theme.
theme = "light"
"""

SECOND_PHRASE = """
[[wake_phrases]]
phrase = "hey computer"
kind = "text_match"
# The second phrase's own comment.
threshold = 0.5
target = "ack"
"""

LEFTOVER = b"the synthetic-only model of the earlier runs"
CANDIDATE = b"the candidate, trained on the owner's voice"
SECOND = b"a second candidate"


class Repo:
    """A checkout's layout under `tmp_path`: the config file and the model files."""

    def __init__(self, root: Path, config_text: str = CONFIG_TEXT) -> None:
        self.root = root
        self.config = root / "config" / "autobot.toml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(config_text, encoding="utf-8")
        self.models = root / "models" / "wakeword"
        self.models.mkdir(parents=True)
        self.store = ConfigStore(self.config)

    def paths(self, index: int = 0) -> install.Paths:
        return install.paths(self.root, self.store.load().wake_phrases[index])

    def candidate(
        self, content: bytes, threshold: float | None, phrase: str = "autobot"
    ) -> None:
        (self.models / "candidate.onnx").write_bytes(content)
        if threshold is not None:
            evaluation = {"phrase": phrase, "recommended_threshold": threshold}
            (self.models / "candidate.json").write_text(json.dumps(evaluation))

    def model(self, name: str, content: bytes, threshold: float | None) -> None:
        (self.models / f"{name}.onnx").write_bytes(content)
        if threshold is not None:
            (self.models / f"{name}.json").write_text(
                json.dumps({"phrase": "autobot", "recommended_threshold": threshold})
            )

    def files(self) -> dict[str, bytes]:
        """Every file under the models directory and the config file, by name."""
        found = {path.name: path.read_bytes() for path in self.models.iterdir()}
        found.update({p.name: p.read_bytes() for p in self.config.parent.iterdir()})
        return found

    def threshold_of(self, name: str) -> float:
        value = json.loads((self.models / name).read_text())["recommended_threshold"]
        assert isinstance(value, float)
        return value

    def config_lines(self) -> list[str]:
        """The `[[wake_phrases]]` lines that an install writes."""
        return [
            line
            for line in self.config.read_text().splitlines()
            if line.startswith(("kind", "model", "threshold"))
        ]


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


# --- what is installed -------------------------------------------------------


def test_the_paths_of_a_fresh_install(repo: Repo) -> None:
    paths = repo.paths()
    models = repo.root / "models" / "wakeword"
    assert paths.phrase == "autobot"
    assert paths.candidate == models / "candidate.onnx"
    assert paths.candidate_json == models / "candidate.json"
    # No model path is configured yet: the phrase's name under models/wakeword.
    assert paths.target == models / "autobot.onnx"
    assert paths.target_json == models / "autobot.json"
    assert paths.previous == models / "autobot.prev.onnx"
    assert paths.previous_json == models / "autobot.prev.json"
    assert paths.model_setting == "models/wakeword/autobot.onnx"


def test_the_target_follows_the_configured_model_path(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "openwakeword"').replace(
        'model = ""', 'model = "models/custom/wake.onnx"'
    )
    repo = Repo(tmp_path, text)
    paths = repo.paths()
    assert paths.target == tmp_path / "models" / "custom" / "wake.onnx"
    assert paths.previous == tmp_path / "models" / "custom" / "wake.prev.onnx"
    assert paths.model_setting == "models/custom/wake.onnx"
    # There is one candidate, wherever a phrase keeps its model.
    assert paths.candidate == tmp_path / "models" / "wakeword" / "candidate.onnx"


def test_a_leftover_is_never_the_installed_model(repo: Repo) -> None:
    config = repo.store.load()
    assert install.installed_model(config, repo.root) is None
    assert install.leftover_model(config, repo.root) is None
    # A model file at the model path, with `text_match` configured.
    repo.model("autobot", LEFTOVER, 0.8)
    repo.model("autobot.prev", b"older still", 0.9)
    assert install.installed_model(config, repo.root) is None
    assert install.leftover_model(config, repo.root) == repo.paths().target
    # A `.prev` beside a leftover is not something to revert to.
    assert install.previous_model(config, repo.root) is None
    assert not install.candidate_exists(repo.paths())


def test_a_configured_model_whose_file_is_gone_is_not_installed(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "openwakeword"').replace(
        'model = ""', 'model = "models/wakeword/autobot.onnx"'
    )
    repo = Repo(tmp_path, text)
    config = repo.store.load()
    assert install.installed_model(config, repo.root) is None
    repo.model("autobot", LEFTOVER, 0.8)
    assert install.installed_model(config, repo.root) == repo.paths().target
    assert install.leftover_model(config, repo.root) is None


# --- installing --------------------------------------------------------------


def test_installing_keeps_the_leftover_and_writes_the_config(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.candidate(CANDIDATE, 0.85)
    assert install.candidate_exists(repo.paths())
    result = install.install_candidate(repo.paths(), repo.store, 0)

    assert repo.config_lines() == [
        'kind = "openwakeword"',
        'model = "models/wakeword/autobot.onnx"',
        "threshold = 0.85  # the fresh-install value",
    ]
    # Every comment is still there, and nothing else in the file moved.
    before, after = CONFIG_TEXT.splitlines(), repo.config.read_text().splitlines()
    assert len(before) == len(after)
    changed = [
        number
        for number, pair in enumerate(zip(before, after, strict=True))
        if pair[0] != pair[1]
    ]
    assert len(changed) == 3
    assert [line for line in after if line.lstrip().startswith("#")] == [
        line for line in before if line.lstrip().startswith("#")
    ]
    # The candidate is in place with its evaluation; the leftover is kept
    # with its own as the previous model; nothing temporary is left.
    assert repo.files().keys() == {
        "autobot.onnx",
        "autobot.json",
        "autobot.prev.onnx",
        "autobot.prev.json",
        "autobot.toml",
    }
    files = repo.files()
    assert files["autobot.onnx"] == CANDIDATE
    assert files["autobot.prev.onnx"] == LEFTOVER
    assert repo.threshold_of("autobot.json") == 0.85
    assert repo.threshold_of("autobot.prev.json") == 0.8

    assert (result.threshold, result.kept_previous) == (0.85, True)
    assert result.previous_has_evaluation
    assert result.message == (
        "installed the candidate as models/wakeword/autobot.onnx at threshold 0.85; "
        "the model it replaced is kept as autobot.prev.onnx"
    )
    phrase = result.config.wake_phrases[0]
    assert (phrase.kind, phrase.model, phrase.threshold) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
        0.85,
    )
    assert load_config(repo.config).wake_phrases[0] == phrase
    assert install.installed_model(result.config, repo.root) == repo.paths().target
    assert install.previous_model(result.config, repo.root) == repo.paths().previous
    assert not install.candidate_exists(repo.paths())


def test_installing_with_nothing_there_keeps_nothing(repo: Repo) -> None:
    repo.candidate(CANDIDATE, 0.9)
    result = install.install_candidate(repo.paths(), repo.store, 0)
    assert not result.kept_previous and not result.previous_has_evaluation
    assert result.message.endswith("there was no model before it")
    assert repo.files().keys() == {"autobot.onnx", "autobot.json", "autobot.toml"}
    assert install.previous_model(result.config, repo.root) is None
    with pytest.raises(InstallError, match="no previous model to revert to"):
        install.revert(repo.paths(), repo.store, 0)


def test_a_leftover_with_no_evaluation_is_kept_alone(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, None)
    # An older previous model's evaluation must not be taken for the leftover's.
    repo.model("autobot.prev", b"older still", 0.6)
    repo.candidate(CANDIDATE, 0.85)
    result = install.install_candidate(repo.paths(), repo.store, 0)
    assert result.kept_previous and not result.previous_has_evaluation
    assert "without an evaluation" in result.message
    files = repo.files()
    assert files.keys() == {
        "autobot.onnx",
        "autobot.json",
        "autobot.prev.onnx",
        "autobot.toml",
    }
    assert files["autobot.prev.onnx"] == LEFTOVER

    # Reverting to it leaves the threshold as it is, and says so.
    reverted = install.revert(repo.paths(), repo.store, 0)
    assert not reverted.threshold_restored
    assert reverted.threshold == 0.85
    assert reverted.message == (
        "reverted to the previous model as models/wakeword/autobot.onnx; it has no "
        "evaluation, so the threshold stays at 0.85"
    )
    assert repo.config_lines()[2] == "threshold = 0.85  # the fresh-install value"
    files = repo.files()
    assert files["autobot.onnx"] == LEFTOVER
    assert files["autobot.prev.onnx"] == CANDIDATE
    # The candidate's evaluation went with it; none is left beside the leftover.
    assert files.keys() == {
        "autobot.onnx",
        "autobot.prev.onnx",
        "autobot.prev.json",
        "autobot.toml",
    }
    assert repo.threshold_of("autobot.prev.json") == 0.85


def test_a_second_install_keeps_only_the_model_it_replaces(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.candidate(CANDIDATE, 0.85)
    install.install_candidate(repo.paths(), repo.store, 0)
    repo.candidate(SECOND, 0.7)
    install.install_candidate(repo.paths(), repo.store, 0)
    files = repo.files()
    assert files["autobot.onnx"] == SECOND
    assert files["autobot.prev.onnx"] == CANDIDATE
    assert (
        repo.threshold_of("autobot.json"),
        repo.threshold_of("autobot.prev.json"),
    ) == (
        0.7,
        0.85,
    )
    assert LEFTOVER not in files.values()
    assert repo.config_lines()[2] == "threshold = 0.7  # the fresh-install value"
    assert len(files) == 5


# --- reverting ---------------------------------------------------------------


def test_reverting_restores_the_file_the_evaluation_and_the_threshold(
    repo: Repo,
) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.candidate(CANDIDATE, 0.85)
    install.install_candidate(repo.paths(), repo.store, 0)

    reverted = install.revert(repo.paths(), repo.store, 0)
    assert (reverted.threshold, reverted.threshold_restored) == (0.8, True)
    assert reverted.message == (
        "reverted to the previous model as models/wakeword/autobot.onnx at threshold "
        "0.8; the model it replaced is kept as autobot.prev.onnx"
    )
    files = repo.files()
    assert files["autobot.onnx"] == LEFTOVER
    assert files["autobot.prev.onnx"] == CANDIDATE
    assert repo.threshold_of("autobot.json") == 0.8
    assert repo.threshold_of("autobot.prev.json") == 0.85
    assert len(files) == 5
    # The model reverted to is now the one the configuration runs.
    assert repo.config_lines() == [
        'kind = "openwakeword"',
        'model = "models/wakeword/autobot.onnx"',
        "threshold = 0.8  # the fresh-install value",
    ]

    # A second revert undoes the first.
    again = install.revert(repo.paths(), repo.store, 0)
    assert again.threshold == 0.85
    files = repo.files()
    assert files["autobot.onnx"] == CANDIDATE
    assert files["autobot.prev.onnx"] == LEFTOVER
    assert repo.threshold_of("autobot.json") == 0.85
    assert len(files) == 5


def test_there_is_nothing_to_revert_while_nothing_is_installed(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.model("autobot.prev", b"older still", 0.9)
    before = repo.files()
    with pytest.raises(InstallError) as error:
        install.revert(repo.paths(), repo.store, 0)
    assert str(error.value) == (
        "no model is installed for 'autobot', so there is nothing to revert"
    )
    assert repo.files() == before


# --- what cannot be installed ------------------------------------------------


def test_a_missing_candidate_is_one_line_and_changes_nothing(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    before = repo.files()
    with pytest.raises(InstallError, match="no candidate to install") as error:
        install.install_candidate(repo.paths(), repo.store, 0)
    assert "candidate.onnx is missing" in str(error.value)
    assert "\n" not in str(error.value)
    assert repo.files() == before


@pytest.mark.parametrize(
    ("evaluation", "message"),
    [
        (None, "the candidate has no evaluation"),
        ("not json", "cannot read the evaluation"),
        ("[1, 2]", "is not a JSON object"),
        ('{"phrase": "autobot"}', "recommends no threshold"),
        ('{"recommended_threshold": true}', "recommends no threshold"),
        ('{"recommended_threshold": 1.5}', "not above 0 and at most 1"),
        (
            '{"phrase": "hey computer", "recommended_threshold": 0.8}',
            "trained for 'hey computer', not 'autobot'",
        ),
    ],
)
def test_a_candidate_without_a_usable_evaluation_is_not_installed(
    repo: Repo, evaluation: str | None, message: str
) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.candidate(CANDIDATE, None)
    if evaluation is not None:
        (repo.models / "candidate.json").write_text(evaluation)
    before = repo.files()
    with pytest.raises(InstallError, match=message) as error:
        install.install_candidate(repo.paths(), repo.store, 0)
    assert "\n" not in str(error.value)
    assert repo.files() == before


def test_a_config_that_cannot_take_the_change_stops_before_any_file_moves(
    repo: Repo,
) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.candidate(CANDIDATE, 0.85)
    # No second `[[wake_phrases]]` entry to write to.
    before = repo.files()
    with pytest.raises(InstallError, match="nothing was changed") as error:
        install.install_candidate(repo.paths(), repo.store, 1)
    assert "\n" not in str(error.value)
    assert repo.files() == before


class FailingStore(ConfigStore):
    """A config file that validates and then cannot be written."""

    def update(self, *changes: Change) -> None:  # type: ignore[override]
        raise ConfigEditError("cannot write config file: Read-only file system")


def test_a_failed_config_write_puts_every_file_back(repo: Repo) -> None:
    repo.model("autobot", LEFTOVER, 0.8)
    repo.model("autobot.prev", b"older still", 0.9)
    repo.candidate(CANDIDATE, 0.85)
    before = repo.files()
    failing = FailingStore(repo.config)
    with pytest.raises(InstallError) as error:
        install.install_candidate(repo.paths(), failing, 0)
    assert str(error.value) == (
        "could not install the candidate, nothing was changed: cannot write config "
        "file: Read-only file system"
    )
    assert repo.files() == before

    # And the same for a revert of a finished install.
    install.install_candidate(repo.paths(), repo.store, 0)
    installed = repo.files()
    with pytest.raises(InstallError, match="could not revert .* nothing was changed"):
        install.revert(repo.paths(), failing, 0)
    assert repo.files() == installed


def test_a_revert_with_a_previous_model_but_no_evaluation_anywhere(repo: Repo) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "openwakeword"').replace(
        'model = ""', 'model = "models/wakeword/autobot.onnx"'
    )
    repo.config.write_text(text)
    repo.model("autobot", CANDIDATE, None)
    repo.model("autobot.prev", LEFTOVER, None)
    reverted = install.revert(repo.paths(), repo.store, 0)
    assert not reverted.threshold_restored and reverted.threshold == 0.5
    assert repo.files().keys() == {"autobot.onnx", "autobot.prev.onnx", "autobot.toml"}
    assert repo.files()["autobot.onnx"] == LEFTOVER


# --- which phrase ------------------------------------------------------------


def test_the_index_names_the_wake_phrase_entry(tmp_path: Path) -> None:
    repo = Repo(tmp_path, CONFIG_TEXT.replace("\n[ack]", SECOND_PHRASE + "\n[ack]"))
    config = repo.store.load()
    assert [phrase.phrase for phrase in config.wake_phrases] == [
        "autobot",
        "hey computer",
    ]
    paths = repo.paths(1)
    assert paths.target == repo.models / "hey_computer.onnx"
    assert paths.previous == repo.models / "hey_computer.prev.onnx"
    repo.candidate(CANDIDATE, 0.75, phrase="hey computer")
    # The candidate is for the second phrase: the first will not take it.
    with pytest.raises(InstallError, match="trained for 'hey computer'"):
        install.install_candidate(repo.paths(0), repo.store, 0)
    result = install.install_candidate(paths, repo.store, 1)
    first, second = result.config.wake_phrases
    assert (first.kind, first.model, first.threshold) == ("text_match", "", 0.5)
    assert (second.kind, second.model, second.threshold) == (
        "openwakeword",
        "models/wakeword/hey_computer.onnx",
        0.75,
    )
    assert "# The second phrase's own comment." in repo.config.read_text()
    assert install.installed_model(result.config, repo.root) is None
    assert install.installed_model(result.config, repo.root, 1) == paths.target


# --- the threshold an install writes ------------------------------------------


def test_an_install_writes_the_threshold_it_is_given(tmp_path: Path) -> None:
    # The interface judged the candidate at 0.7, on the held-out set as it is
    # now; the training, on the set as it was then, had recommended 0.5.
    repo = Repo(tmp_path)
    repo.candidate(CANDIDATE, 0.5)
    result = install.install_candidate(repo.paths(), repo.store, 0, threshold=0.7)
    assert result.threshold == 0.7
    assert "at threshold 0.7;" in result.message
    assert result.config.wake_phrases[0].threshold == 0.7
    assert "threshold = 0.7  # the fresh-install value" in repo.config_lines()
    # The evaluation keeps what the training recommended, and gains what the
    # model was installed at.
    evaluation = json.loads((repo.models / "autobot.json").read_text())
    assert evaluation == {
        "phrase": "autobot",
        "recommended_threshold": 0.5,
        "installed_threshold": 0.7,
    }
    assert install.installed_threshold_of(repo.models / "autobot.json") == 0.7
    assert not list(repo.models.glob(".*"))


def test_the_same_threshold_leaves_the_evaluation_as_it_was(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    repo.candidate(CANDIDATE, 0.6)
    before = (repo.models / "candidate.json").read_bytes()
    install.install_candidate(repo.paths(), repo.store, 0, threshold=0.6)
    assert (repo.models / "autobot.json").read_bytes() == before
    assert install.installed_threshold_of(repo.models / "autobot.json") == 0.6


def test_a_given_threshold_stands_in_for_an_evaluation_without_one(
    tmp_path: Path,
) -> None:
    repo = Repo(tmp_path)
    (repo.models / "candidate.onnx").write_bytes(CANDIDATE)
    (repo.models / "candidate.json").write_text(json.dumps({"phrase": "autobot"}))
    with pytest.raises(InstallError, match="recommends no threshold"):
        install.install_candidate(repo.paths(), repo.store, 0)
    result = install.install_candidate(repo.paths(), repo.store, 0, threshold=0.8)
    assert result.config.wake_phrases[0].threshold == 0.8
    assert install.installed_threshold_of(repo.models / "autobot.json") == 0.8


@pytest.mark.parametrize("threshold", [0.0, -0.2, 1.5])
def test_a_threshold_out_of_range_installs_nothing(
    tmp_path: Path, threshold: float
) -> None:
    repo = Repo(tmp_path)
    repo.candidate(CANDIDATE, 0.6)
    before = repo.files()
    with pytest.raises(InstallError, match="not above 0 and at most 1"):
        install.install_candidate(repo.paths(), repo.store, 0, threshold=threshold)
    assert repo.files() == before


def test_a_revert_restores_the_threshold_a_model_was_installed_at(
    tmp_path: Path,
) -> None:
    repo = Repo(tmp_path)
    repo.candidate(CANDIDATE, 0.5)
    install.install_candidate(repo.paths(), repo.store, 0, threshold=0.7)
    repo.candidate(SECOND, 0.6)
    second = install.install_candidate(repo.paths(), repo.store, 0, threshold=0.9)
    assert second.config.wake_phrases[0].threshold == 0.9
    # Back to the first model: at 0.7, where it ran, not at its file's 0.5.
    back = install.revert(repo.paths(), repo.store, 0)
    assert (back.threshold, back.threshold_restored) == (0.7, True)
    assert repo.files()["autobot.onnx"] == CANDIDATE
    # And the revert of the revert: the second model, at 0.9 again.
    again = install.revert(repo.paths(), repo.store, 0)
    assert again.threshold == 0.9
    assert repo.files()["autobot.onnx"] == SECOND
    assert repo.threshold_of("autobot.json") == 0.6
