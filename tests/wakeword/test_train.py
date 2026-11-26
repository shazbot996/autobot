"""`autobot.wakeword`: the parts of the training that need no synthesis and no torch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autobot.listener.models import enforce_offline
from autobot.wakeword import train
from autobot.wakeword.data import (
    HELDOUT_VOICES,
    PHRASE_TABLES,
    TRAIN_VOICES,
    PhraseTable,
    phrase_table,
)
from autobot.wakeword.features import embedding_count
from autobot.wakeword.train import (
    KIND_GENERIC,
    KIND_HARD,
    KIND_NOISE,
    KIND_POSITIVE,
    SPLIT_HELDOUT,
    SPLIT_TRAIN,
    Shard,
    TrainError,
)

# Before anything here can import onnxruntime.
enforce_offline()


# --- the plan ----------------------------------------------------------------


def test_the_plan_is_the_same_every_time() -> None:
    assert train.plan_shards(2.5) == train.plan_shards(2.5)


def test_shard_names_are_unique() -> None:
    names = [shard.name for shard in train.plan_shards(3)]
    assert len(names) == len(set(names))


def test_a_larger_scale_only_adds_training_shards() -> None:
    small, large = train.plan_shards(1), train.plan_shards(4)
    assert set(small) < set(large)
    added = set(large) - set(small)
    assert {shard.split for shard in added} == {SPLIT_TRAIN}
    assert len(added) > 2 * len(small)


def test_the_held_out_shards_do_not_depend_on_the_scale() -> None:
    def held(scale: float) -> list[Shard]:
        return [s for s in train.plan_shards(scale) if s.split == SPLIT_HELDOUT]

    assert held(0.2) == held(1) == held(8)
    clips = [shard for shard in held(1) if not shard.noise]
    assert sum(shard.positives for shard in clips) == pytest.approx(2000, abs=5)
    assert {shard.voice for shard in clips} == set(HELDOUT_VOICES)


def test_training_and_held_out_voices_never_mix() -> None:
    assert not set(TRAIN_VOICES) & set(HELDOUT_VOICES)
    for shard in train.plan_shards(2):
        if shard.voice:
            voices = TRAIN_VOICES if shard.split == SPLIT_TRAIN else HELDOUT_VOICES
            assert shard.voice in voices
            assert shard.name.startswith(f"{shard.split}-{shard.voice}-")


def test_every_training_voice_gets_a_shard_at_any_scale() -> None:
    shards = train.plan_shards(0.01)
    assert {s.voice for s in shards if s.split == SPLIT_TRAIN and s.voice} == set(
        TRAIN_VOICES
    )
    assert sum(1 for s in shards if s.noise and s.split == SPLIT_TRAIN) == 1


def test_a_training_shard_holds_a_fixed_number_of_clips() -> None:
    shards = [s for s in train.plan_shards(2) if s.split == SPLIT_TRAIN and s.voice]
    assert {s.clips for s in shards} == {train.SHARD_CLIPS}
    # About 23,000 clips per unit of scale.
    assert sum(s.clips for s in shards) == pytest.approx(46000, rel=0.05)


def test_the_scale_must_be_positive() -> None:
    with pytest.raises(TrainError, match="above 0"):
        train.plan_shards(0)


# --- resuming ----------------------------------------------------------------


def test_a_rerun_skips_the_shards_that_are_finished(tmp_path: Path) -> None:
    shards = train.plan_shards(1)[:4]
    (tmp_path / "clips").mkdir()

    def path_of(shard: Shard) -> Path:
        return train.clip_path(tmp_path, shard)

    assert train.pending(shards, path_of) == shards
    path_of(shards[1]).write_bytes(b"done")
    path_of(shards[3]).write_bytes(b"done")
    assert train.pending(shards, path_of) == [shards[0], shards[2]]


def test_a_half_written_shard_is_not_taken_for_a_finished_one(tmp_path: Path) -> None:
    shard = train.plan_shards(1)[0]
    (tmp_path / "feats").mkdir()
    path = train.feat_path(tmp_path, shard)
    # What a killed `save_npz` leaves behind.
    path.with_name(path.name + ".tmp.npz").write_bytes(b"half")
    assert train.pending([shard], lambda s: train.feat_path(tmp_path, s)) == [shard]
    train.save_npz(path, X=np.zeros((1, 28, 96), dtype=np.float16))
    assert train.pending([shard], lambda s: train.feat_path(tmp_path, s)) == []
    assert not path.with_name(path.name + ".tmp.npz").exists()


def test_feature_files_are_named_by_split(tmp_path: Path) -> None:
    by_split = {shard.split: shard for shard in train.plan_shards(1)}
    assert train.feat_path(tmp_path, by_split[SPLIT_TRAIN]).name.endswith(".seq.npz")
    assert not train.feat_path(tmp_path, by_split[SPLIT_HELDOUT]).name.endswith(
        ".seq.npz"
    )


def test_a_checkpoint_resumes_only_the_run_that_made_it() -> None:
    wanted = {"phrase": "autobot", "steps": 1000, "hidden": 128, "sequences": 9}
    saved = {**wanted, "step": 400, "net": object()}
    assert train.checkpoint_matches(saved, wanted)
    assert not train.checkpoint_matches({**saved, "steps": 2000}, wanted)
    assert not train.checkpoint_matches({**saved, "sequences": 10}, wanted)
    assert not train.checkpoint_matches({"step": 400}, wanted)


def test_each_network_size_and_schedule_has_its_own_checkpoint(tmp_path: Path) -> None:
    def run(hidden: int, steps: int) -> train.Run:
        return train.Run(
            phrase="autobot",
            output=tmp_path / "autobot.onnx",
            models=tmp_path,
            work=tmp_path / "work",
            scale=1,
            steps=steps,
            hidden=hidden,
            threads=1,
        )

    paths = {run(128, 1000).checkpoint, run(256, 1000).checkpoint}
    paths.add(run(256, 2000).checkpoint)
    assert len(paths) == 3
    assert all(path.parent == tmp_path / "work" for path in paths)
    assert run(256, 1000).checkpoint == run(256, 1000).checkpoint


def test_each_loss_has_its_own_checkpoint(tmp_path: Path) -> None:
    def run(**settings: Any) -> train.Run:
        return train.Run(
            phrase="autobot",
            output=tmp_path / "autobot.onnx",
            models=tmp_path,
            work=tmp_path / "work",
            scale=1,
            steps=1000,
            hidden=128,
            threads=1,
            **settings,
        )

    # The first model's settings keep the first model's checkpoint name.
    assert run().checkpoint.name == "ckpt-128-1000.pt"
    assert run().variant == ""
    paths = {
        run().checkpoint,
        run(exclude_near=True).checkpoint,
        run(exclude_near=True, hard_topk=0.0).checkpoint,
        run(exclude_near=True, negative_weight=0.5).checkpoint,
        run(hard_topk=0.0).checkpoint,
    }
    assert len(paths) == 5
    # Where the result is written does not make it a different training.
    assert run(candidate=True).checkpoint == run().checkpoint


def test_a_checkpoint_does_not_resume_a_run_with_another_loss() -> None:
    wanted = {
        "phrase": "autobot",
        "steps": 1000,
        "excluded": [],
        "hard_topk": 1.0,
        "negative_weight": 1.0,
    }
    # A checkpoint from before the settings existed was trained with these.
    old = {"phrase": "autobot", "steps": 1000, "step": 400}
    assert train.checkpoint_matches(old, wanted)
    assert not train.checkpoint_matches(old, {**wanted, "excluded": ["auto boot"]})
    assert not train.checkpoint_matches(old, {**wanted, "hard_topk": 0.0})
    assert not train.checkpoint_matches(old, {**wanted, "negative_weight": 0.5})
    new = {**old, "excluded": ["auto boot"], "hard_topk": 0.0, "negative_weight": 1.0}
    assert not train.checkpoint_matches(new, wanted)
    assert train.checkpoint_matches(
        new, {**wanted, "excluded": ["auto boot"], "hard_topk": 0.0}
    )


# --- what the clips say ------------------------------------------------------


def texts_of(shard: Shard, seed: int = 7) -> list[train.Text]:
    return train.make_texts(np.random.default_rng(seed), shard, phrase_table("autobot"))


def test_texts_are_the_same_for_the_same_seed() -> None:
    shard = Shard("train-x-000", SPLIT_TRAIN, "x", positives=20, hard=20, generic=10)
    assert texts_of(shard) == texts_of(shard)
    assert texts_of(shard) != texts_of(shard, seed=8)


def test_texts_mark_where_the_key_words_are() -> None:
    shard = Shard("train-x-000", SPLIT_TRAIN, "x", positives=60, hard=60, generic=30)
    texts = texts_of(shard)
    assert [kind for kind, *_ in texts] == (
        [KIND_POSITIVE] * 60 + [KIND_HARD] * 60 + [KIND_GENERIC] * 30
    )
    table = phrase_table("autobot")
    for kind, text, first, after, hard_id in texts:
        words = [word.strip(",.?!").lower() for word in text.split()]
        if kind == KIND_POSITIVE:
            assert "".join(words[first:after]) == "autobot"
            assert hard_id == -1
        elif kind == KIND_HARD:
            assert " ".join(words[first:after]) == table.hard[hard_id]
            assert "autobot" not in "".join(words)
        else:
            assert (first, after, hard_id) == (-1, -1, -1)
            assert "autobot" not in "".join(words)


def test_training_texts_leave_the_evaluation_only_negatives_alone() -> None:
    table = phrase_table("autobot")
    train_shard = Shard("train-x-000", SPLIT_TRAIN, "x", hard=400)
    held_shard = Shard("heldout-x-000", SPLIT_HELDOUT, "x", hard=400)
    trained = {table.hard[hard_id] for *_, hard_id in texts_of(train_shard)}
    held = {table.hard[hard_id] for *_, hard_id in texts_of(held_shard)}
    assert not trained & table.eval_only
    assert held & table.eval_only


def test_hard_negative_ids_of_the_first_model_have_not_moved() -> None:
    # Cached shards store these ids: the table is append-only.
    hard = PHRASE_TABLES["autobot"].hard
    assert (hard[0], hard[38], hard[59], hard[70], hard[74], hard[75]) == (
        "auto",
        "autobus",
        "autoboard",
        "water bottle",
        "a lot of bots",
        "ought to bite",
    )


def test_what_the_first_model_fired_on_is_trained_on_now() -> None:
    trained = set(phrase_table("autobot").trained)
    assert {"a lot of bots", "water bottle", "odd about", "auto box"} <= trained
    assert {"auto bar", "autobus", "bottle", "lots of bots"} <= trained


def test_a_phrase_without_a_table_gets_a_generic_one() -> None:
    table = phrase_table("Hey  Computer")
    assert table.spellings == ("hey computer",)
    assert table.hard[:2] == ("hey", "computer")
    assert table.eval_only == frozenset()
    assert phrase_table("AutoBot") is PHRASE_TABLES["autobot"]


# --- labels ------------------------------------------------------------------


def test_a_window_is_positive_just_after_the_phrase_ends() -> None:
    # The phrase runs from 2.0 s to 2.6 s of a 3 s window (16 kHz samples).
    meta = np.array([[KIND_POSITIVE, -1, 0, 32000, 41600]], dtype=np.int64)
    labels = train.label_windows(meta)
    assert labels.shape == (1, train.SEQUENCE_INPUTS)
    # Inputs end at 31600, 32880, ... Five end before half the phrase is heard,
    # three while it is being said, five within half a second of its end.
    assert labels[0].tolist() == [0] * 5 + [-1] * 3 + [1] * 5


def test_inputs_long_after_the_phrase_are_ignored() -> None:
    # The phrase ends at 1.6 s; inputs end from 0.375 s after it onwards.
    meta = np.array([[KIND_POSITIVE, -1, 0, 16000, 25600]], dtype=np.int64)
    assert train.label_windows(meta)[0].tolist() == [1, 1] + [-1] * 11


def test_inputs_that_hold_half_the_phrase_are_ignored() -> None:
    # The phrase ends at 2.95 s, after the last input does.
    meta = np.array([[KIND_POSITIVE, -1, 0, 37600, 47200]], dtype=np.int64)
    assert train.label_windows(meta)[0].tolist() == [0] * 9 + [-1] * 4


def test_everything_else_is_negative_throughout() -> None:
    meta = np.array(
        [
            [KIND_HARD, 3, 0, 16000, 25600],
            [KIND_GENERIC, -1, 1, -1, -1],
            [KIND_NOISE, -1, -1, -1, -1],
        ],
        dtype=np.int64,
    )
    assert not train.label_windows(meta).any()


# --- hard negatives that sound the same as the phrase ------------------------


def test_the_excluded_phrases_are_near_homophones_that_are_trained_on() -> None:
    table = phrase_table("autobot")
    assert table.train_excluded == {
        *("auto bought", "auto bought it", "ought to bought", "oughta but"),
        *("auto boot", "auto boat", "auto bob", "auto dot", "auto pot"),
    }
    assert table.train_excluded <= set(table.trained)
    assert not table.train_excluded & table.eval_only
    # Different words stay trained negatives.
    kept = {"auto", "robot", "autobus", "water bottle", "auto box", "autoboard"}
    assert kept <= set(table.trained) - table.train_excluded
    assert phrase_table("hey computer").train_excluded == frozenset()


def test_the_excluded_phrases_are_still_synthesised() -> None:
    # The exclusion is a filter on windows: the cached clips are unchanged.
    table = phrase_table("autobot")
    shard = Shard("train-x-000", SPLIT_TRAIN, "x", hard=4000)
    said = {table.hard[hard_id] for *_, hard_id in texts_of(shard)}
    assert table.train_excluded <= said


def test_excluded_ids_follow_the_texts_when_the_table_grows() -> None:
    table = phrase_table("autobot")
    ids = train.excluded_ids(table)
    assert sorted(table.hard[i] for i in ids) == sorted(table.train_excluded)
    grown = PhraseTable(
        spellings=table.spellings,
        hard=(*table.hard, "new phrase", "auto boot"),
        eval_only=table.eval_only,
        train_excluded=table.train_excluded,
    )
    # Old ids have not moved, and a repeat of an excluded text is excluded too.
    assert train.excluded_ids(grown) == [*ids, len(table.hard) + 1]


def near_meta() -> tuple[train.Int64, int, int]:
    table = phrase_table("autobot")
    near, far = table.hard.index("auto bought"), table.hard.index("autobus")
    meta = np.array(
        [
            [KIND_POSITIVE, -1, 0, 32000, 41600],
            [KIND_HARD, near, 1, 32000, 41600],
            [KIND_HARD, far, 2, 32000, 41600],
            [KIND_GENERIC, -1, 3, -1, -1],
            [KIND_NOISE, -1, -1, -1, -1],
            # A stray id on a window that is not a hard negative is not looked at.
            [KIND_POSITIVE, near, 4, 32000, 41600],
        ],
        dtype=np.int64,
    )
    return meta, near, far


def test_only_hard_negative_windows_of_excluded_phrases_are_chosen() -> None:
    meta, _near, _far = near_meta()
    chosen = train.excluded_windows(meta, phrase_table("autobot"))
    assert chosen.tolist() == [False, True, False, False, False, False]
    generic = phrase_table("hey computer")
    assert not train.excluded_windows(meta, generic).any()


def test_excluded_windows_are_dropped_from_training_not_relabelled() -> None:
    meta, _near, _far = near_meta()
    table = phrase_table("autobot")
    plain = train.label_windows(meta)
    kept = train.training_inputs(meta, table, exclude_near=False)
    assert np.array_equal(kept, plain)
    assert not kept[1].any()
    dropped = train.training_inputs(meta, table, exclude_near=True)
    # Every input of the window is ignored: neither negative nor positive.
    assert dropped[1].tolist() == [-1] * train.SEQUENCE_INPUTS
    others = [0, 2, 3, 4, 5]
    assert np.array_equal(dropped[others], plain[others])
    assert (dropped[0] == 1).sum() == 5
    assert not dropped[2].any()


def test_features_can_be_loaded_for_chosen_windows_only(tmp_path: Path) -> None:
    shards = [s for s in train.plan_shards(0.01) if s.split == SPLIT_TRAIN][:2]
    (tmp_path / "feats").mkdir()
    meta, near, _far = near_meta()
    table = phrase_table("autobot")
    for number, shard in enumerate(shards):
        features = np.full((len(meta), 28, 96), number, dtype=np.float16)
        features[:, 0, 0] = np.arange(len(meta))
        train.save_npz(train.feat_path(tmp_path, shard), X=features, meta=meta)
    everything, all_meta = train.load_features(tmp_path, shards, SPLIT_TRAIN)
    assert everything.shape == (12, 28, 96)
    assert len(all_meta) == 12
    chosen, chosen_meta = train.load_features(
        tmp_path, shards, SPLIT_TRAIN, lambda m: train.excluded_windows(m, table)
    )
    assert chosen.shape == (2, 28, 96)
    assert chosen[:, 0, 0].tolist() == [1, 1]
    assert chosen[:, 1, 0].tolist() == [0, 1]
    assert chosen_meta[:, 1].tolist() == [near, near]
    nothing, no_meta = train.load_features(
        tmp_path, shards, SPLIT_TRAIN, lambda m: np.zeros(len(m), dtype=np.bool_)
    )
    assert len(nothing) == len(no_meta) == 0


def test_a_training_window_is_scored_only_when_the_phrase_ends_early_enough() -> None:
    # The last input ends at 46960; half a second before it is 38960.
    last_input_end = train.INPUT_END_SAMPLE + 1280 * (train.SEQUENCE_INPUTS - 1)
    assert train.NEAR_LATEST_KEY_END == last_input_end - 8000 == 38960


# --- features and scores -----------------------------------------------------


@pytest.mark.parametrize(
    ("samples", "embeddings"), [(32000, 16), (48000, 28), (64000, 41), (1280, 0)]
)
def test_embedding_counts(samples: int, embeddings: int) -> None:
    assert embedding_count(samples) == embeddings


def test_window_sizes_agree_with_the_feature_extractor() -> None:
    inputs = embedding_count(train.SEQUENCE_SAMPLES) - 16 + 1
    assert inputs == train.SEQUENCE_INPUTS


def test_clip_features_match_the_streaming_engine_in_shape() -> None:
    from autobot.wakeword.features import ClipFeatures

    clips = np.random.default_rng(0).integers(-3000, 3000, (3, 48000)).astype(np.int16)
    features = ClipFeatures().embed_clips(clips)
    assert features.shape == (3, 28, 96)
    assert features.dtype == np.float32
    # Each clip is embedded on its own: the batch does not leak between them.
    assert np.array_equal(ClipFeatures().embed_clips(clips[1:2])[0], features[1])


def test_stream_inputs_slide_one_embedding_at_a_time() -> None:
    features = np.arange(20 * 96, dtype=np.float32).reshape(20, 96)
    inputs = train.stream_inputs(features)
    assert inputs.shape == (5, 16, 96)
    assert np.array_equal(inputs[2], features[2:18])


def test_hits_close_together_count_as_one_event() -> None:
    scores = np.zeros(200)
    scores[[10, 11, 12, 30, 100]] = 0.9
    # 30 is within 25 frames of 12; 100 is not.
    assert train.count_events(scores, 0.5) == 2
    assert train.count_events(scores, 0.95) == 0
    assert train.count_events(scores, 0.5, refractory=5) == 3


def row(threshold: float, recall: float, false_accepts: float) -> dict[str, float]:
    return {
        "threshold": threshold,
        "recall": recall,
        "val_fa_per_hour": false_accepts,
    }


def test_the_recommendation_is_the_best_recall_within_the_target() -> None:
    rows = [row(0.2, 0.99, 0.1), row(0.5, 0.95, 0.4), row(0.7, 0.9, 0.2)]
    # 0.2 is under the lowest threshold that is offered.
    assert train.recommend(rows)["threshold"] == 0.5


def test_a_model_that_misses_the_target_keeps_a_usable_recall() -> None:
    rows = [row(0.5, 0.85, 9.9), row(0.9, 0.74, 3.9), row(0.95, 0.69, 2.2)]
    rows.append(row(0.99, 0.31, 1.4))
    assert train.recommend(rows)["threshold"] == 0.95
    assert train.recommend([row(0.9, 0.3, 5.0), row(0.99, 0.1, 2.0)])["threshold"] == (
        0.99
    )


def test_the_time_left_comes_from_the_average_so_far() -> None:
    assert train.eta_minutes(started=0.0, done=10, total=40, now=600.0) == 30.0
    assert train.eta_minutes(started=0.0, done=0, total=40, now=600.0) == 0.0


# --- names and installing ----------------------------------------------------


def test_slug() -> None:
    assert train.slug("Hey, Auto-Bot!") == "hey_auto_bot"
    assert train.slug("autobot") == "autobot"
    assert train.slug("?!") == "phrase"


def test_installing_keeps_the_model_it_replaces(tmp_path: Path) -> None:
    output = tmp_path / "autobot.onnx"
    first, second = tmp_path / "a.tmp", tmp_path / "b.tmp"
    first.write_bytes(b"model one")
    train.install(first, {"recommended_threshold": 0.9}, output)
    assert output.read_bytes() == b"model one"
    assert not first.exists()
    assert not train.previous_path(output).exists()
    second.write_bytes(b"model two")
    train.install(second, {"recommended_threshold": 0.7}, output)
    assert output.read_bytes() == b"model two"
    assert (tmp_path / "autobot.prev.onnx").read_bytes() == b"model one"
    metrics = json.loads((tmp_path / "autobot.json").read_text())
    previous = json.loads((tmp_path / "autobot.prev.json").read_text())
    assert (metrics["recommended_threshold"], previous["recommended_threshold"]) == (
        0.7,
        0.9,
    )
    # Nothing temporary is left beside the model.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "autobot.json",
        "autobot.onnx",
        "autobot.prev.json",
        "autobot.prev.onnx",
    ]


def test_a_missing_training_asset_is_one_line_naming_the_fetch(tmp_path: Path) -> None:
    run = train.Run(
        phrase="autobot",
        output=tmp_path / "autobot.onnx",
        models=tmp_path,
        work=tmp_path / "work",
        scale=1,
        steps=10,
        hidden=8,
        threads=1,
    )
    shard = Shard("train-en_US-lessac-medium-000", SPLIT_TRAIN, "en_US-lessac-medium")
    with pytest.raises(TrainError) as error:
        train.check_assets(run, [shard])
    assert "en_US-lessac-medium.onnx" in str(error.value)
    assert "make fetch-models TRAIN=1" in str(error.value)
    assert "\n" not in str(error.value)


def test_the_command_line_reports_a_bad_scale_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = train.main(
        ["--scale", "0", "--work", str(tmp_path), "--output", str(tmp_path / "m.onnx")]
    )
    assert status == 2
    assert capsys.readouterr().err == "--scale must be above 0\n"


def test_a_named_run_is_a_candidate_beside_the_installed_model(tmp_path: Path) -> None:
    arguments = ["--output", str(tmp_path / "autobot.onnx"), "--work", str(tmp_path)]
    plain = train.resolve_run(train.build_parser().parse_args(arguments))
    assert plain.output == tmp_path / "autobot.onnx"
    assert not plain.candidate
    # The first model's training, unless the options say otherwise.
    assert (plain.exclude_near, plain.hard_topk, plain.negative_weight) == (
        False,
        1.0,
        1.0,
    )
    options = ["--name", "exp_b", "--exclude-near-homophones"]
    options += ["--hard-topk-weight", "0", "--negative-weight", "0.5"]
    named = train.resolve_run(train.build_parser().parse_args(arguments + options))
    assert named.output == tmp_path / "candidates" / "exp_b.onnx"
    assert named.candidate
    assert (named.exclude_near, named.hard_topk, named.negative_weight) == (
        True,
        0.0,
        0.5,
    )
    assert named.checkpoint != plain.checkpoint
    assert named.checkpoint.parent == plain.checkpoint.parent


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--name", "../autobot"], "--name must be"),
        (["--name", ""], "--name must be"),
        (["--hard-topk-weight", "-1"], "--hard-topk-weight"),
        (["--negative-weight", "0"], "--negative-weight"),
    ],
)
def test_bad_training_options_are_reported_in_one_line(
    options: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--output", str(tmp_path / "m.onnx"), "--work", str(tmp_path)]
    assert train.main(arguments + options) == 2
    error = capsys.readouterr().err
    assert message in error
    assert error.count("\n") == 1
    assert not (tmp_path / "candidates").exists()


def test_the_model_file_must_be_onnx(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert train.main(["--output", str(tmp_path / "model.bin")]) == 2
    assert "must end in .onnx" in capsys.readouterr().err
