"""`autobot.wakeword.dataset`: the voice dataset, its manifest and its split."""

import dataclasses
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from autobot.tui.audio import Clip
from autobot.wakeword.dataset import (
    FIXTURE_LEAD_IN,
    FIXTURE_NAMES,
    FIXTURE_TAIL,
    HELDOUT,
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    MANIFEST,
    SOURCE_MONITOR,
    SOURCE_SESSION,
    TRAIN,
    VOICE_DIR,
    ClipRecord,
    DatasetError,
    HeldoutSize,
    LabelCount,
    VoiceDataset,
    ambient_slices,
    clip_file_name,
    export_fixture,
    recorded_now,
    sanitise_clip_name,
    split,
    voiced_span,
)

RATE = 16000
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
AT = "2026-11-26T17:40:03-05:00"
# Worked out with hashlib alone, outside the module under test.
PROMPT_IDS = ("bare-01", "bare-02", "bare-03", "bare-11", "bare-12")
EXPECTED_PROMPT_SIDES = ["train", "train", "train", "heldout", "heldout"]
EXPECTED_FILE_SIDES = ["train", "train", "train", "heldout", "heldout"]


def clip(seconds: float, value: int = 100) -> Clip:
    return Clip(np.full(int(seconds * RATE), value, dtype=np.int16), RATE)


def record(**changes: str) -> ClipRecord:
    base = ClipRecord(
        file="",
        phrase="autobot",
        prompt="autobot",
        prompt_id="bare-01",
        label=LABEL_POSITIVE,
        source=SOURCE_SESSION,
        speaker="charles",
        device="pipewire",
        recorded_at=AT,
    )
    return dataclasses.replace(base, **changes)


def manifest_lines(dataset: VoiceDataset) -> list[dict[str, str]]:
    text = dataset.manifest.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_the_paths() -> None:
    assert VOICE_DIR == Path("data/voice")
    assert MANIFEST == Path("data/voice/manifest.jsonl")
    assert VoiceDataset(Path("/x") / VOICE_DIR).manifest == Path("/x") / MANIFEST


def test_an_empty_dataset_has_no_records_and_creates_nothing(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    assert dataset.records() == []
    assert dataset.for_phrase("autobot") == []
    assert dataset.last_speaker() == ""
    assert dataset.counts() == {
        LABEL_POSITIVE: LabelCount(),
        LABEL_NEGATIVE: LabelCount(),
        LABEL_AMBIENT: LabelCount(),
    }
    assert dataset.heldout_size("autobot") == HeldoutSize()
    assert not dataset.root.exists()


def test_a_clip_round_trips_through_the_wav_and_the_manifest(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    audio = Clip(np.arange(-800, 800, dtype=np.int16), RATE)
    stored = dataset.add(audio, record())

    assert stored == record(file="charles-bare-01-20261126-174003.wav")
    assert dataset.records() == [stored]
    assert manifest_lines(dataset) == [
        {
            "file": "charles-bare-01-20261126-174003.wav",
            "phrase": "autobot",
            "prompt": "autobot",
            "prompt_id": "bare-01",
            "label": "positive",
            "source": "session",
            "speaker": "charles",
            "device": "pipewire",
            "recorded_at": AT,
        }
    ]
    with wave.open(str(dataset.path(stored)), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            RATE,
        )
    loaded = dataset.load(stored)
    assert loaded.sample_rate == RATE
    assert np.array_equal(loaded.samples, audio.samples)
    assert dataset.seconds(stored) == pytest.approx(0.1)
    assert dataset.seconds(stored.file) == pytest.approx(0.1)
    # A second dataset object on the same directory reads the same thing.
    assert VoiceDataset(dataset.root).records() == [stored]


def test_file_names(tmp_path: Path) -> None:
    assert clip_file_name(record()) == "charles-bare-01-20261126-174003.wav"
    # A clip with no prompt identifier is named after its source.
    assert (
        clip_file_name(record(prompt_id="", source=SOURCE_MONITOR))
        == "charles-monitor-20261126-174003.wav"
    )
    # Whatever is typed, the name stays inside the directory and keeps its stamp.
    name = clip_file_name(record(speaker="../Ana María de la " + "x" * 80))
    assert name == sanitise_clip_name(name)
    assert "/" not in name and name.endswith("-bare-01-20261126-174003.wav")
    with pytest.raises(DatasetError, match="needs a letter or a digit"):
        clip_file_name(record(speaker="..."))
    with pytest.raises(DatasetError, match="recorded_at"):
        clip_file_name(record(recorded_at="yesterday"))
    assert clip_file_name(record(recorded_at=recorded_now())).startswith(
        "charles-bare-01-20"
    )


def test_a_taken_name_gets_a_number_and_nothing_is_overwritten(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    first = dataset.add(clip(0.1, 1), record())
    second = dataset.add(clip(0.2, 2), record())
    third = dataset.add(clip(0.3, 3), record())
    assert [first.file, second.file, third.file] == [
        "charles-bare-01-20261126-174003.wav",
        "charles-bare-01-20261126-174003-2.wav",
        "charles-bare-01-20261126-174003-3.wav",
    ]
    assert [int(dataset.load(item).samples[0]) for item in dataset.records()] == [
        1,
        2,
        3,
    ]
    # A name given by the caller is kept, made safe.
    named = dataset.add(clip(0.1), record(file="../My Take"))
    assert named.file == "My-Take.wav"


def test_a_bad_record_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    for bad, message in (
        (record(label="maybe"), "label 'maybe' is not one of"),
        (record(source="phone"), "source 'phone' is not one of"),
        (record(speaker="  "), "needs a speaker's name"),
    ):
        with pytest.raises(DatasetError, match=message) as raised:
            dataset.add(clip(0.1), bad)
        assert "\n" not in str(raised.value)
    assert not dataset.root.exists()


def test_for_phrase_counts_and_the_last_speaker(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    dataset.add(clip(3.0), record())
    dataset.add(clip(6.0), record(label=LABEL_NEGATIVE, prompt_id="hard-01"))
    dataset.add(clip(60.0), record(label=LABEL_AMBIENT, prompt_id="ambient-01"))
    other = dataset.add(
        clip(30.0), record(phrase="Computer", speaker="sam", prompt_id="bare-02")
    )

    assert len(dataset.records()) == 4
    assert len(dataset.for_phrase("AUTOBOT")) == 3
    assert dataset.for_phrase("computer") == [other]
    assert dataset.last_speaker() == "sam"

    counts = dataset.counts("autobot")
    assert {label: count.clips for label, count in counts.items()} == {
        LABEL_POSITIVE: 1,
        LABEL_NEGATIVE: 1,
        LABEL_AMBIENT: 1,
    }
    assert counts[LABEL_POSITIVE].minutes == pytest.approx(0.05)
    assert counts[LABEL_NEGATIVE].minutes == pytest.approx(0.1)
    assert counts[LABEL_AMBIENT].minutes == pytest.approx(1.0)
    everything = dataset.counts()
    assert everything[LABEL_POSITIVE].clips == 2
    assert everything[LABEL_POSITIVE].minutes == pytest.approx(0.55)


# --- the split ----------------------------------------------------------------


def test_every_take_of_a_prompt_lands_on_the_same_side() -> None:
    for number in range(100):
        prompt_id = f"prompt-{number:03d}"
        sides = {
            split(record(prompt_id=prompt_id, file=f"take-{take}.wav", recorded_at=at))
            for take, at in enumerate((AT, "2027-01-02T03:04:05+00:00", ""))
        }
        assert len(sides) == 1


def test_about_one_prompt_in_five_is_held_out() -> None:
    sides = [split(record(prompt_id=f"prompt-{number:03d}")) for number in range(100)]
    assert set(sides) == {TRAIN, HELDOUT}
    assert 10 <= sides.count(HELDOUT) <= 30


def test_the_split_is_the_documented_hash_and_never_moves() -> None:
    # sha1("charles/bare-01") % 5 and friends, worked out once by hand: a
    # change to the rule would move clips between training and evaluation.
    assert [
        split(record(prompt_id=prompt_id)) for prompt_id in PROMPT_IDS
    ] == EXPECTED_PROMPT_SIDES
    # The speaker is part of the key.
    sides = {split(record(speaker=f"speaker-{n}", prompt_id="x")) for n in range(40)}
    assert sides == {TRAIN, HELDOUT}
    # The label is not: a negative splits as a positive does.
    for number in range(20):
        prompt_id = f"p{number}"
        assert split(record(prompt_id=prompt_id)) == split(
            record(prompt_id=prompt_id, label=LABEL_NEGATIVE)
        )


def test_a_clip_with_no_prompt_id_is_split_by_its_file_name() -> None:
    def side(name: str, **changes: str) -> str:
        return split(record(prompt_id="", file=name, **changes))

    names = [f"charles-monitor-20261126-{number:06d}.wav" for number in range(100)]
    sides = [side(name) for name in names]
    assert 10 <= sides.count(HELDOUT) <= 30
    # Nothing but the file name decides: not the speaker, not the source.
    assert sides == [
        side(name, speaker="someone else", source=SOURCE_MONITOR) for name in names
    ]
    assert [side(name) for name in names[8:13]] == EXPECTED_FILE_SIDES


def test_the_last_fifth_of_an_ambient_clip_is_held_out() -> None:
    ambient = record(label=LABEL_AMBIENT, prompt_id="ambient-01")
    assert ambient_slices(ambient, 60.0) == ((0.0, 48.0), (48.0, 60.0))
    train, heldout = ambient_slices(ambient, 3600.0)
    assert train == (0.0, 2880.0)
    assert heldout == (2880.0, 3600.0)
    assert ambient_slices(ambient, 0.0) == ((0.0, 0.0), (0.0, 0.0))
    # An ambient clip has no side, and a spoken clip has no slices.
    with pytest.raises(ValueError, match="split by time"):
        split(ambient)
    with pytest.raises(ValueError, match="only an ambient clip"):
        ambient_slices(record(), 60.0)


def test_the_heldout_size(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    expected_positives = expected_negatives = 0
    for number in range(30):
        positive = dataset.add(clip(0.1), record(prompt_id=f"bare-{number:02d}"))
        negative = dataset.add(
            clip(0.1), record(prompt_id=f"hard-{number:02d}", label=LABEL_NEGATIVE)
        )
        expected_positives += split(positive) == HELDOUT
        expected_negatives += split(negative) == HELDOUT
    # A second take of every held-out prompt is held out too.
    for stored in dataset.records()[:10]:
        if split(stored) == HELDOUT:
            dataset.add(clip(0.1), dataclasses.replace(stored, file=""))
            if stored.label == LABEL_POSITIVE:
                expected_positives += 1
            else:
                expected_negatives += 1
    dataset.add(clip(60.0), record(label=LABEL_AMBIENT, prompt_id="ambient-01"))
    dataset.add(clip(30.0), record(label=LABEL_AMBIENT, prompt_id="ambient-02"))
    # Another phrase's clips do not count.
    dataset.add(clip(60.0), record(phrase="computer", label=LABEL_AMBIENT))

    size = dataset.heldout_size("autobot")
    assert (size.positives, size.negatives) == (expected_positives, expected_negatives)
    assert 0 < size.positives < 30 and 0 < size.negatives < 30
    assert size.ambient_minutes == pytest.approx((12.0 + 6.0) / 60)


# --- removal and strays --------------------------------------------------------


def test_removing_a_clip_deletes_the_wav_and_its_line(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    first = dataset.add(clip(0.1), record())
    second = dataset.add(clip(0.1), record(prompt_id="bare-02"))
    third = dataset.add(clip(0.1), record(prompt_id="bare-03"))
    before = dataset.manifest.read_text(encoding="utf-8").splitlines()

    dataset.remove(second.file)

    assert dataset.records() == [first, third]
    assert not dataset.path(second).exists()
    assert dataset.path(first).exists() and dataset.path(third).exists()
    # The other lines are kept byte for byte, and no temporary file is left.
    assert dataset.manifest.read_text(encoding="utf-8").splitlines() == [
        before[0],
        before[2],
    ]
    assert sorted(entry.name for entry in dataset.root.iterdir()) == sorted(
        [first.file, third.file, "manifest.jsonl"]
    )
    with pytest.raises(DatasetError, match="no clip named"):
        dataset.remove(second.file)
    with pytest.raises(DatasetError, match="no clip named"):
        dataset.remove("../manifest.jsonl")
    assert dataset.records() == [first, third]


def test_a_stray_wav_is_ignored_and_a_line_without_a_wav_is_left_out(
    tmp_path: Path,
) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    kept = dataset.add(clip(0.1), record())
    gone = dataset.add(clip(0.1), record(prompt_id="bare-02"))
    stray = dataset.root / "stray.wav"
    stray.write_bytes(dataset.path(kept).read_bytes())
    dataset.path(gone).unlink()

    assert dataset.records() == [kept]
    assert dataset.counts()[LABEL_POSITIVE].clips == 1
    assert stray.exists()
    # The stray's name is still never given to a new clip.
    again = dataset.add(clip(0.1), record(file="stray"))
    assert again.file == "stray-2.wav"


def test_add_file_moves_a_finished_recording_into_place(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    first = dataset.add(clip(0.1), record(label=LABEL_AMBIENT, prompt_id="long"))
    part = dataset.root / ".recording.part"
    part.write_bytes(dataset.path(first).read_bytes())

    stored = dataset.add_file(
        part, record(label=LABEL_AMBIENT, prompt_id="long", file="")
    )
    assert stored.file == "charles-long-20261126-174003-2.wav"
    assert not part.exists()
    assert dataset.records() == [first, stored]
    assert dataset.seconds(stored) == pytest.approx(0.1)
    with pytest.raises(DatasetError, match="cannot write into"):
        dataset.add_file(part, record(prompt_id="long"))
    assert dataset.records() == [first, stored]
    assert not dataset.path("charles-long-20261126-174003-3.wav").exists()


def test_a_damaged_manifest_is_a_one_line_error(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    dataset.add(clip(0.1), record())
    good = dataset.manifest.read_text(encoding="utf-8")
    for bad, message in (
        ("{not json\n", "line 2 is not valid JSON"),
        ("[1, 2]\n", "line 2 is not a JSON object"),
        ('{"file": "a.wav"}\n', "line 2: phrase is missing or not a string"),
        (good.replace("positive", "maybe"), "line 2: label 'maybe'"),
        (
            good.replace("charles-bare", "../charles-bare"),
            "is not a plain file name",
        ),
    ):
        dataset.manifest.write_text(good + bad, encoding="utf-8")
        with pytest.raises(DatasetError, match=message) as raised:
            dataset.records()
        assert "\n" not in str(raised.value)
    # Blank lines are not damage, and a line without a prompt_id has an empty one.
    line = json.loads(good)
    del line["prompt_id"]
    dataset.manifest.write_text("\n" + json.dumps(line) + "\n\n", encoding="utf-8")
    assert [item.prompt_id for item in dataset.records()] == [""]


def test_a_file_that_is_not_a_mono_16_bit_wav_is_named(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    stored = dataset.add(clip(0.1), record())
    dataset.path(stored).write_bytes(b"not a wav")
    with pytest.raises(DatasetError, match="as a WAV file"):
        dataset.seconds(stored)
    with pytest.raises(DatasetError, match="as a WAV file"):
        dataset.load(stored)


# --- exporting a fixture ----------------------------------------------------------


def spoken(lead: float, speech: float, tail: float, noise: int = 20) -> Clip:
    """A clip with quiet room noise around a loud stretch standing for speech."""
    rng = np.random.default_rng(7)
    total = round((lead + speech + tail) * RATE)
    samples = rng.integers(-noise, noise + 1, total).astype(np.int16)
    first = round(lead * RATE)
    count = round(speech * RATE)
    samples[first : first + count] = (
        6000 * np.sin(2 * np.pi * 220 * np.arange(count) / RATE)
    ).astype(np.int16)
    return Clip(samples, RATE)


def read_fixture(path: Path) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            RATE,
        )
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(
            np.int16
        )


def test_the_fixture_names_are_the_ones_the_readme_lists() -> None:
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `([^`]+\.wav)` \|", readme, flags=re.MULTILINE)
    assert listed, "the fixtures README has no table of file names"
    assert tuple(listed) == FIXTURE_NAMES
    # The README sends the reader to the Dataset screen, not to the Audio screen.
    assert "Dataset screen" in readme
    assert "from the Audio screen" not in readme


def test_the_lead_in_and_tail_are_what_the_readme_asks_for() -> None:
    assert (FIXTURE_LEAD_IN, FIXTURE_TAIL) == (0.5, 1.0)


def test_voiced_span_finds_the_speech() -> None:
    first, end = voiced_span(spoken(0.3, 0.6, 0.4).samples, RATE) or (0, 0)
    assert first / RATE == pytest.approx(0.3, abs=0.02)
    assert end / RATE == pytest.approx(0.9, abs=0.02)
    # A clip that is level throughout has no speech to find.
    assert voiced_span(clip(1.0).samples, RATE) is None
    assert voiced_span(np.zeros(RATE, dtype=np.int16), RATE) is None
    assert voiced_span(np.zeros(10, dtype=np.int16), RATE) is None


def test_export_pads_a_clip_that_is_cut_close(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    source = spoken(0.1, 0.6, 0.2)
    stored = dataset.add(source, record())
    before = dataset.path(stored).read_bytes()
    target = tmp_path / "fixtures" / "autobot.wav"

    assert export_fixture(stored, dataset.path(stored), target) == target

    samples = read_fixture(target)
    span = voiced_span(samples, RATE)
    assert span is not None
    first, end = span
    assert first / RATE >= 0.5 - 0.02
    assert (len(samples) - end) / RATE >= 1.0 - 0.02
    # Only silence was added, in front and behind: the recording sits whole
    # between the two.
    source_span = voiced_span(source.samples, RATE)
    assert source_span is not None
    front = round(0.5 * RATE) - source_span[0]
    back = round(1.0 * RATE) - (len(source.samples) - source_span[1])
    assert front == pytest.approx(0.4 * RATE, abs=0.02 * RATE)
    assert back == pytest.approx(0.8 * RATE, abs=0.02 * RATE)
    assert len(samples) == front + len(source.samples) + back
    assert not samples[:front].any()
    assert not samples[len(samples) - back :].any()
    assert np.array_equal(samples[front : front + len(source.samples)], source.samples)
    # The clip in the dataset is untouched.
    assert dataset.path(stored).read_bytes() == before


def test_export_leaves_a_clip_with_room_as_it_is(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    source = spoken(0.7, 0.5, 1.2)
    stored = dataset.add(source, record())
    target = tmp_path / "fixtures" / "hey_autobot.wav"
    export_fixture(stored, dataset.path(stored), target)
    assert np.array_equal(read_fixture(target), source.samples)


def test_export_takes_other_lead_in_and_tail(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    source = spoken(0.1, 0.5, 0.1)
    stored = dataset.add(source, record())
    target = tmp_path / "out.wav"
    export_fixture(stored, dataset.path(stored), target, lead_in=0.0, tail=0.0)
    assert np.array_equal(read_fixture(target), source.samples)
    export_fixture(stored, dataset.path(stored), target, lead_in=1.0, tail=2.0)
    assert len(read_fixture(target)) == pytest.approx(3.5 * RATE, abs=0.04 * RATE)


def test_export_writes_an_ambient_clip_as_it_is(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    # Room noise with a click in it: nothing to centre.
    source = spoken(0.1, 0.05, 0.1)
    stored = dataset.add(source, record(label=LABEL_AMBIENT, prompt_id="ambient-01"))
    target = tmp_path / "fixtures" / "room_noise.wav"
    export_fixture(stored, dataset.path(stored), target)
    assert np.array_equal(read_fixture(target), source.samples)


def test_export_replaces_an_existing_fixture_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    stored = dataset.add(spoken(0.6, 0.5, 1.1), record())
    target = tmp_path / "fixtures" / "autobot.wav"
    target.parent.mkdir()
    target.write_bytes(b"the old fixture")
    export_fixture(stored, dataset.path(stored), target)
    assert len(read_fixture(target)) == round(2.2 * RATE)
    assert [path.name for path in target.parent.iterdir()] == ["autobot.wav"]
    # A committed file is readable by everyone.
    assert target.stat().st_mode & 0o777 == 0o644


def test_export_failures_are_one_line_and_leave_the_old_fixture(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    stored = dataset.add(clip(0.5), record())
    target = tmp_path / "fixtures" / "autobot.wav"
    target.parent.mkdir()
    target.write_bytes(b"the old fixture")

    with pytest.raises(DatasetError, match="cannot read .* as a WAV file"):
        export_fixture(stored, tmp_path / "voice" / "missing.wav", target)

    other_rate = tmp_path / "voice" / "other.wav"
    with wave.open(str(other_rate), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x00\x00" * 480)
    with pytest.raises(DatasetError, match="is not a 16000 Hz mono 16-bit WAV file"):
        export_fixture(stored, other_rate, target)

    empty = dataset.add(clip(0.0), record())
    with pytest.raises(DatasetError, match="holds no audio"):
        export_fixture(empty, dataset.path(empty), target)

    blocker = tmp_path / "blocker"
    blocker.write_text("a file", encoding="utf-8")
    with pytest.raises(DatasetError, match="cannot write") as raised:
        export_fixture(stored, dataset.path(stored), blocker / "autobot.wav")
    assert "\n" not in str(raised.value)
    assert target.read_bytes() == b"the old fixture"
