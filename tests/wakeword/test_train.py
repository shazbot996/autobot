"""`autobot.wakeword`: the parts of the training that need no synthesis and no torch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autobot.config import resolve_repo_path
from autobot.listener.models import enforce_offline
from autobot.tui.audio import Clip
from autobot.wakeword import train
from autobot.wakeword.data import (
    HELDOUT_VOICES,
    PHRASE_TABLES,
    TRAIN_VOICES,
    PhraseTable,
    phrase_table,
)
from autobot.wakeword.dataset import (
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    ClipRecord,
    VoiceDataset,
    split,
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
            **{
                "phrase": "autobot",
                "output": tmp_path / "autobot.onnx",
                "models": tmp_path,
                "work": tmp_path / "work",
                "scale": 1,
                "steps": 1000,
                "hidden": 128,
                "threads": 1,
                **settings,
            }
        )

    # The first model's settings keep the first model's checkpoint name.
    first = run(exclude_near=False)
    assert first.checkpoint.name == "ckpt-128-1000.pt"
    assert first.variant == ""
    # The studio's training leaves the near homophones out unless told not to.
    assert run().exclude_near
    assert run().checkpoint.name == "ckpt-128-1000-x1-k1-n1.pt"
    paths = {
        first.checkpoint,
        run().checkpoint,
        run(hard_topk=0.0).checkpoint,
        run(negative_weight=0.5).checkpoint,
        run(exclude_near=False, hard_topk=0.0).checkpoint,
        # A run on real recordings never shares a synthetic-only checkpoint.
        run(real_manifest="abc").checkpoint,
        run(exclude_near=False, real_manifest="abc").checkpoint,
    }
    assert len(paths) == 7
    assert run(real_manifest="abc").checkpoint.name == "ckpt-128-1000-x1-k1-n1-real.pt"
    assert run(exclude_near=False, real_manifest="abc").checkpoint.name == (
        "ckpt-128-1000-real.pt"
    )
    # Where the result is written does not make it a different training.
    assert run(output=tmp_path / "other.onnx").checkpoint == run().checkpoint


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


def test_a_changed_dataset_starts_a_fresh_run() -> None:
    wanted = {"phrase": "autobot", "steps": 1000, "real_manifest": "aaa"}
    assert train.checkpoint_matches({**wanted, "step": 400}, wanted)
    # Another clip in the manifest is another manifest hash.
    assert not train.checkpoint_matches(
        {**wanted, "step": 400}, {**wanted, "real_manifest": "bbb"}
    )
    # A checkpoint from before there were recordings was trained on none.
    old = {"phrase": "autobot", "steps": 1000, "step": 400}
    assert not train.checkpoint_matches(old, wanted)
    assert train.checkpoint_matches(old, {**wanted, "real_manifest": ""})


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


def test_a_run_writes_a_candidate_and_touches_nothing_else(tmp_path: Path) -> None:
    installed = tmp_path / "autobot.onnx"
    installed.write_bytes(b"the installed model")
    (tmp_path / "autobot.json").write_text('{"recommended_threshold": 0.8}\n')
    output = tmp_path / train.CANDIDATE_NAME
    for content, threshold in ((b"candidate one", 0.9), (b"candidate two", 0.7)):
        exported = tmp_path / "candidate.onnx.tmp"
        exported.write_bytes(content)
        train.write_candidate(exported, {"recommended_threshold": threshold}, output)
        assert output.read_bytes() == content
        metrics = json.loads((tmp_path / "candidate.json").read_text())
        assert metrics["recommended_threshold"] == threshold
    # One candidate at a time, no `.prev`, nothing temporary, and the
    # installed model and its evaluation as they were.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "autobot.json",
        "autobot.onnx",
        "candidate.json",
        "candidate.onnx",
    ]
    assert installed.read_bytes() == b"the installed model"
    assert not hasattr(train, "install")


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


def test_the_default_output_is_the_candidate(tmp_path: Path) -> None:
    run = train.resolve_run(train.build_parser().parse_args(["--work", str(tmp_path)]))
    assert run.output == resolve_repo_path("models/wakeword/candidate.onnx")
    assert run.output.name == train.CANDIDATE_NAME
    # The owner's recordings, unless another directory is named.
    assert run.dataset == resolve_repo_path("data/voice")
    elsewhere = train.resolve_run(
        train.build_parser().parse_args(
            ["--work", str(tmp_path), "--dataset", str(tmp_path / "voice")]
        )
    )
    assert elsewhere.dataset == tmp_path / "voice"


def test_a_named_run_is_an_experiment_beside_the_candidate(tmp_path: Path) -> None:
    arguments = ["--output", str(tmp_path / "autobot.onnx"), "--work", str(tmp_path)]
    plain = train.resolve_run(train.build_parser().parse_args(arguments))
    assert plain.output == tmp_path / "autobot.onnx"
    # The best known recipe, unless the options say otherwise: the near
    # homophones left out, both loss weights at 1.
    assert (plain.exclude_near, plain.hard_topk, plain.negative_weight) == (
        True,
        1.0,
        1.0,
    )
    options = ["--name", "exp_b", "--no-exclude-near-homophones"]
    options += ["--hard-topk-weight", "0", "--negative-weight", "0.5"]
    named = train.resolve_run(train.build_parser().parse_args(arguments + options))
    assert named.output == tmp_path / "candidates" / "exp_b.onnx"
    assert (named.exclude_near, named.hard_topk, named.negative_weight) == (
        False,
        0.0,
        0.5,
    )
    assert named.checkpoint != plain.checkpoint
    assert named.checkpoint.parent == plain.checkpoint.parent
    # The first model's training is still there to be asked for.
    first = train.resolve_run(
        train.build_parser().parse_args([*arguments, "--no-exclude-near-homophones"])
    )
    assert first.variant == ""


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


# --- the real recordings -----------------------------------------------------

RATE = 16000
SPEAKER = "tester"


def speech_like(seconds: float, lead: float = 0.5, tail: float = 0.7) -> train.Int16:
    """Quiet, then a tone burst standing in for speech, then quiet."""
    rng = np.random.default_rng(int(seconds * 1000))
    burst = 4000 * np.sin(2 * np.pi * 300 * np.arange(int(seconds * RATE)) / RATE)
    audio = rng.normal(0, 30, int((lead + seconds + tail) * RATE))
    audio[int(lead * RATE) : int(lead * RATE) + len(burst)] += burst
    return audio.astype(np.int16)


def room(seconds: float, seed: int = 0) -> train.Int16:
    return (
        np.random.default_rng(seed).normal(0, 40, int(seconds * RATE)).astype(np.int16)
    )


def record_for(
    label: str, prompt: str, prompt_id: str, number: int, speaker: str = SPEAKER
) -> ClipRecord:
    return ClipRecord(
        file="",
        phrase="autobot",
        prompt=prompt,
        prompt_id=prompt_id,
        label=label,
        source="session",
        speaker=speaker,
        device="test",
        recorded_at=f"2026-11-26T18:{number // 60:02d}:{number % 60:02d}+00:00",
    )


def prompt_ids(side: str, count: int, prefix: str) -> list[str]:
    """Prompt identifiers the dataset's split puts on one side, for `SPEAKER`."""
    found: list[str] = []
    number = 0
    while len(found) < count:
        identifier = f"{prefix}-{number:03d}"
        if split(record_for(LABEL_POSITIVE, "", identifier, 0)) == side:
            found.append(identifier)
        number += 1
    return found


def small_dataset(root: Path) -> VoiceDataset:
    """Six positives, six negatives and two ambient recordings, on known sides."""
    dataset = VoiceDataset(root)
    number = 0

    def add(label: str, prompt: str, prompt_id: str, audio: train.Int16) -> None:
        nonlocal number
        number += 1
        dataset.add(Clip(audio, RATE), record_for(label, prompt, prompt_id, number))

    prompts = ("autobot", "hey autobot", "yo autobot", "autobot, what time is it")
    for index, identifier in enumerate(prompt_ids("train", 4, "pos")):
        add(LABEL_POSITIVE, prompts[index], identifier, speech_like(0.6 + 0.1 * index))
    for identifier in prompt_ids("heldout", 2, "pos"):
        add(LABEL_POSITIVE, "autobot", identifier, speech_like(0.65))
    texts = ("auto boot", "autopilot", "how much does that cost", "auto bought")
    for index, identifier in enumerate(prompt_ids("train", 4, "neg")):
        add(LABEL_NEGATIVE, texts[index], identifier, speech_like(0.5 + 0.2 * index))
    for identifier in prompt_ids("heldout", 2, "neg"):
        add(LABEL_NEGATIVE, "robot", identifier, speech_like(0.55))
    add(LABEL_AMBIENT, "say nothing", "ambient-01", room(60.0, 1))
    add(LABEL_AMBIENT, "say nothing", "ambient-02", room(20.0, 2))
    return dataset


def test_the_real_split_lists_are_disjoint_and_cover_the_manifest(
    tmp_path: Path,
) -> None:
    dataset = small_dataset(tmp_path)
    real = train.load_real(dataset, "autobot")
    table = phrase_table("autobot")
    spoken = {r.file for r in dataset.records() if r.label != LABEL_AMBIENT}
    ambient = {r.file for r in dataset.records() if r.label == LABEL_AMBIENT}

    # Trained on the near homophones too: two lists, and nothing else.
    section = train.real_section(real, table, exclude_near=False)
    trained, held = set(section["trained"]), set(section["heldout"])
    assert not trained & held
    assert trained | held == spoken
    assert section["excluded"] == []
    assert (len(trained), len(held)) == (8, 4)
    assert "note" not in section

    # The default leaves the recordings of near homophones out of the
    # training: they are named on their own, and are on neither list.
    section = train.real_section(real, table, exclude_near=True)
    trained, held = set(section["trained"]), set(section["heldout"])
    excluded = set(section["excluded"])
    assert len(excluded) == 2  # "auto boot" and "auto bought"
    assert not trained & held and not trained & excluded and not held & excluded
    assert trained | held | excluded == spoken
    assert section["counts"] == {
        "trained_positives": 4,
        "trained_negatives": 2,
        "excluded_negatives": 2,
        "heldout_positives": 2,
        "heldout_negatives": 2,
        "ambient_clips": 2,
        "ambient_trained_minutes": pytest.approx((48 + 16) / 60),
        "ambient_heldout_minutes": pytest.approx((12 + 4) / 60),
    }
    # The ambient recordings are on no list of clips: they are split by time.
    assert not ambient & (trained | held | excluded)
    assert {entry["file"] for entry in section["ambient_trained"]} == ambient
    assert section["manifest"] == real.manifest
    # What the dataset itself says is held out.
    size = dataset.heldout_size("autobot")
    assert (size.positives, size.negatives) == (2, 2)
    assert size.ambient_minutes == pytest.approx(real.heldout_ambient_minutes)


def test_two_takes_of_one_prompt_land_on_the_same_list(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path)
    for number in range(40):
        # Two takes of each of twenty prompts, recorded at different times.
        record = record_for(
            LABEL_POSITIVE, "autobot", f"bare-{number % 20:02d}", number
        )
        dataset.add(Clip(speech_like(0.6), RATE), record)
    real = train.load_real(dataset, "autobot")
    section = train.real_section(real, phrase_table("autobot"), exclude_near=True)
    side = {file: "train" for file in section["trained"]}
    side.update({file: "heldout" for file in section["heldout"]})
    by_prompt: dict[str, set[str]] = {}
    for record in dataset.records():
        by_prompt.setdefault(record.prompt_id, set()).add(side[record.file])
    assert len(by_prompt) == 20
    assert all(len(sides) == 1 for sides in by_prompt.values())
    assert {"train", "heldout"} == set().union(*by_prompt.values())


def test_each_ambient_clip_gives_four_fifths_to_training_and_its_last_fifth_held_out(
    tmp_path: Path,
) -> None:
    real = train.load_real(small_dataset(tmp_path), "autobot")
    section = train.real_section(real, phrase_table("autobot"), exclude_near=True)
    trained = {e["file"]: (e["start"], e["end"]) for e in section["ambient_trained"]}
    held = {e["file"]: (e["start"], e["end"]) for e in section["ambient_heldout"]}
    assert sorted(trained.values()) == [(0.0, 16.0), (0.0, 48.0)]
    assert sorted(held.values()) == [(16.0, 20.0), (48.0, 60.0)]
    for clip in real.ambient:
        # In samples: the two parts meet, never overlap, and cover the clip.
        assert clip.train[0] == 0
        assert clip.train[1] == clip.heldout[0]
        assert clip.heldout[1] == clip.samples
        assert held[clip.record.file][0] == trained[clip.record.file][1]


def spans_of(dataset: VoiceDataset, real: train.RealData) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    for clip in real.clips:
        if clip.split == SPLIT_TRAIN and clip.record.label == LABEL_POSITIVE:
            start, end, _how = train.key_span(
                dataset.load(clip.record).samples, clip.record, None
            )
            spans[clip.record.file] = (start, end)
    return spans


def test_no_training_window_is_cut_from_held_out_audio(tmp_path: Path) -> None:
    dataset = small_dataset(tmp_path)
    real = train.load_real(dataset, "autobot")
    windows = train.plan_real_windows(
        real,
        phrase_table("autobot"),
        spans_of(dataset, real),
        np.random.default_rng(0),
    )
    held_files = set(real.files(SPLIT_HELDOUT))
    assert held_files
    covered: dict[str, set[int]] = {}
    for window in windows:
        clip = real.clips[window.clip]
        assert window.end - window.start == train.SEQUENCE_SAMPLES
        # No window comes from a held-out clip.
        assert clip.record.file not in held_files
        assert clip.split != SPLIT_HELDOUT
        if clip.split == train.SPLIT_AMBIENT:
            # An ambient window lies wholly inside the training four fifths:
            # not one sample of the held-out last fifth is in it.
            assert clip.train[0] <= window.start
            assert window.end <= clip.train[1] == clip.heldout[0]
            assert not set(range(window.start, window.end, 4000)) & set(
                range(clip.heldout[0], clip.heldout[1], 4000)
            )
            covered.setdefault(clip.record.file, set()).add(window.start)
    # The training part is used whole: 48 s and 16 s in 3 s windows.
    assert sorted(len(starts) for starts in covered.values()) == [5, 16]
    by_kind = {kind: 0 for kind in (KIND_POSITIVE, KIND_HARD, KIND_GENERIC, KIND_NOISE)}
    for window in windows:
        by_kind[window.kind] += 1
    assert by_kind == {
        KIND_POSITIVE: 4 * train.REAL_VARIANTS,
        # "auto boot", "auto bought" and "autopilot" are in the phrase's table.
        KIND_HARD: 3 * train.REAL_VARIANTS,
        KIND_GENERIC: 1 * train.REAL_VARIANTS,
        KIND_NOISE: 21 * train.REAL_VARIANTS,
    }
    # One window of each clip is as recorded, the others are augmented.
    assert sum(not window.augment for window in windows) * train.REAL_VARIANTS == len(
        windows
    )
    # The same plan every time.
    again = train.plan_real_windows(
        real, phrase_table("autobot"), spans_of(dataset, real), np.random.default_rng(0)
    )
    assert again == windows


def test_real_windows_are_labelled_like_the_synthetic_ones(tmp_path: Path) -> None:
    dataset = small_dataset(tmp_path)
    real = train.load_real(dataset, "autobot")
    table = phrase_table("autobot")
    windows = train.plan_real_windows(
        real, table, spans_of(dataset, real), np.random.default_rng(0)
    )
    meta = np.array(
        [(w.kind, w.hard_id, w.clip, w.key_start, w.key_end) for w in windows],
        dtype=np.int64,
    )
    for window in windows:
        if window.kind == KIND_POSITIVE:
            # The phrase ends 1.3 to 2.9 s into the window.
            assert 1.3 * RATE <= window.key_end <= 2.9 * RATE
            assert (
                0
                < window.key_end - window.key_start
                <= train.REAL_PHRASE_SECONDS * RATE
            )
    labels = train.training_inputs(meta, table, exclude_near=True)
    positive = meta[:, 0] == KIND_POSITIVE
    # As with a synthetic window, the inputs that end up to half a second
    # after the phrase are the positives; the first input ends 1.975 s in.
    heard = positive & (meta[:, 4] + 0.5 * RATE >= train.INPUT_END_SAMPLE)
    assert heard.sum() >= 8
    assert (labels[heard] == 1).any(axis=1).all()
    assert not (labels[~positive] == 1).any()
    # The owner saying a near homophone is left out, like a synthetic one.
    near = train.excluded_windows(meta, table)
    assert int(near.sum()) == 2 * train.REAL_VARIANTS
    assert (labels[near] == -1).all()
    assert (train.training_inputs(meta, table, exclude_near=False)[near] == 0).all()


def test_the_manifest_hash_follows_the_dataset(tmp_path: Path) -> None:
    dataset = small_dataset(tmp_path)
    first = train.load_real(dataset, "autobot")
    assert len(first.manifest) == 40
    assert train.load_real(dataset, "autobot") == first
    assert [clip.record.file for clip in first.clips] == sorted(
        record.file for record in dataset.records()
    )
    dataset.add(
        Clip(speech_like(0.6), RATE), record_for(LABEL_POSITIVE, "autobot", "", 300)
    )
    second = train.load_real(dataset, "autobot")
    assert second.manifest != first.manifest
    # A clip recorded for another phrase is not this phrase's.
    assert train.load_real(dataset, "hey computer").clips == ()


def test_a_run_with_no_recordings_is_the_first_models_run(tmp_path: Path) -> None:
    real = train.load_real(VoiceDataset(tmp_path / "voice"), "autobot")
    assert real.clips == () and real.manifest == ""
    table = phrase_table("autobot")
    assert real.describe(table, True) == [
        "real: no real recordings for 'autobot': synthetic clips only"
    ]
    section = train.real_section(real, table, True)
    assert section["note"] == "no real recordings: synthetic clips only"
    assert section["trained"] == section["heldout"] == section["ambient_heldout"] == []
    # The same shards, the same checkpoint and the same batches as before.
    run = train.Run(
        phrase="autobot",
        output=tmp_path / "candidate.onnx",
        models=tmp_path,
        work=tmp_path / "work",
        scale=1,
        steps=1000,
        hidden=128,
        threads=1,
        dataset=tmp_path / "voice",
    )
    assert train.plan_shards(run.scale) == train.plan_shards(1)
    assert run.real_manifest == ""
    assert run.checkpoint.name == "ckpt-128-1000-x1-k1-n1.pt"
    assert train.batch_plan(0, 0) == train.BatchPlan(256, 0, 384, 0)
    assert train.real_feats(run, real).features.shape == (0, 28, 96)
    assert not (tmp_path / "work").exists()


def test_real_inputs_are_mixed_in_only_when_there_are_some() -> None:
    assert train.batch_plan(517, 3805) == train.BatchPlan(128, 128, 256, 128)
    # Recordings of room audio alone give negatives and no positives.
    assert train.batch_plan(0, 40) == train.BatchPlan(256, 0, 256, 128)
    assert train.batch_plan(3, 0) == train.BatchPlan(128, 128, 384, 0)
    for plan in (train.batch_plan(1, 1), train.batch_plan(0, 0)):
        assert plan.positives + plan.real_positives == train.POSITIVE_BATCH == 256
        assert plan.negatives + plan.real_negatives == train.NEGATIVE_BATCH == 384


def test_the_log_line_counts_what_is_trained_on_and_held_out(tmp_path: Path) -> None:
    real = train.load_real(small_dataset(tmp_path), "autobot")
    lines = real.describe(phrase_table("autobot"), exclude_near=True)
    assert lines[0] == "real: 6 training clips, 4 held out, 0.3 min held-out ambient"
    assert "left out 2 negatives" in lines[-1]
    lines = real.describe(phrase_table("autobot"), exclude_near=False)
    assert lines[0] == "real: 8 training clips, 4 held out, 0.3 min held-out ambient"


def test_the_voiced_span_is_where_the_speech_is() -> None:
    start, end = train.voiced_span(speech_like(0.6, lead=0.5, tail=0.9))
    assert abs(start - 0.5 * RATE) <= 640
    assert abs(end - 1.1 * RATE) <= 640
    # A click is not speech, and a pause inside a word does not end it.
    audio = speech_like(0.6, lead=0.5, tail=0.9)
    audio[2 * RATE // 10 : 2 * RATE // 10 + 80] += 9000
    audio[int(0.75 * RATE) : int(0.85 * RATE)] = 0
    assert train.voiced_span(audio) == (start, end)
    # Nothing above the floor: the clip is taken whole.
    assert train.voiced_span(room(2.0)) == (0, 2 * RATE)


def test_whether_a_prompt_ends_with_the_phrase_is_read_from_the_manifest() -> None:
    def last(prompt: str, prompt_id: str = "p-01") -> bool:
        return train.phrase_last(record_for(LABEL_POSITIVE, prompt, prompt_id, 0))

    assert last("autobot") and last("hey autobot") and last("Yo, Autobot.")
    assert not last("autobot, what time is it")
    assert not last("so autobot, what's the news")
    # A clip with no script prompt is not known to end with the phrase.
    assert not last("monitor: missed wake", "")
    assert not last("autobot", "")


def test_the_phrase_is_found_among_transcribed_words() -> None:
    words = [(" Hey", 0.0, 1.06), (" Autobot,", 1.06, 1.68), (" what's", 1.9, 2.14)]
    assert train.find_phrase(words, "autobot") == (1.06, 1.68)
    split_up = [(" so", 0.7, 1.3), (" auto", 1.3, 1.6), (" bot.", 1.6, 1.9)]
    assert train.find_phrase(split_up, "autobot") == (1.3, 1.9)
    misheard = [(" Bottlebot,", 1.16, 1.8), (" who", 2.12, 2.24), (" won", 2.24, 2.4)]
    assert train.find_phrase(misheard, "autobot") is None
    # The word the prompt has after the phrase says where the phrase ended.
    prompt = "autobot, who won the game last night"
    anchored = train.find_phrase(misheard, "autobot", prompt)
    assert anchored is not None
    assert anchored[0] == pytest.approx(1.12) and anchored[1] == 2.12
    assert train.find_phrase(misheard, "autobot", prompt, spelled_only=True) is None
    close = [(" Ottobot", 0.9, 1.5), (" stop.", 1.6, 1.9)]
    assert train.find_phrase(close, "autobot") == (0.9, 1.5)
    assert train.find_phrase([], "autobot", prompt) is None
    assert train.find_phrase([(" stop.", 0.0, 1.8)], "autobot", "autobot, stop") is None


def test_a_sentence_positive_ends_where_the_words_say_not_where_the_speech_does() -> (
    None
):
    audio = speech_like(2.0, lead=0.5, tail=0.8)
    sentence = record_for(LABEL_POSITIVE, "autobot, what time is it", "sentence-01", 1)
    asked: list[str] = []

    def locate(samples: train.Int16, record: ClipRecord) -> tuple[float, float] | None:
        asked.append(record.prompt_id)
        return (0.55, 1.1)

    start, end, how = train.key_span(audio, sentence, locate)
    assert (how, end) == ("words", round(1.1 * RATE))
    assert start <= round(0.6 * RATE)
    # Not found, or no transcriber: the end of the speech, as the plan has it.
    voiced_end = train.voiced_span(audio)[1]
    assert train.key_span(audio, sentence, lambda samples, record: None)[1:] == (
        voiced_end,
        "fallback",
    )
    assert train.key_span(audio, sentence, None)[1:] == (voiced_end, "fallback")
    # A prompt that ends with the phrase is never transcribed.
    asked.clear()
    bare = record_for(LABEL_POSITIVE, "hey autobot", "hey-01", 2)
    assert train.key_span(audio, bare, locate)[1:] == (voiced_end, "span")
    assert asked == []


def fake_banks(work: Path) -> None:
    """The noise and impulse-response banks, small: what `Augmenter` loads."""
    rng = np.random.default_rng(0)
    work.mkdir(parents=True, exist_ok=True)
    train.save_npz(
        work / "noise16k.npz",
        audio=rng.integers(-2000, 2000, (10, 5 * RATE)).astype(np.int16),
        fold=np.arange(10) % 5 + 1,
    )
    responses = np.zeros((20, 400), dtype=np.float32)
    responses[:, 0] = 1.0
    train.save_npz(work / "rir16k.npz", rir=responses)


def test_the_real_features_are_cut_from_the_training_side_and_cached(
    tmp_path: Path,
) -> None:
    pytest.importorskip("scipy", reason="the augmentation needs the 'train' extra")
    dataset = small_dataset(tmp_path / "voice")
    real = train.load_real(dataset, "autobot")
    work = tmp_path / "work"
    fake_banks(work)
    run = train.Run(
        phrase="autobot",
        output=tmp_path / "candidate.onnx",
        models=resolve_repo_path("models"),
        work=work,
        scale=1,
        steps=10,
        hidden=8,
        threads=1,
        dataset=dataset.root,
        real_manifest=real.manifest,
    )
    located: list[str] = []

    def locate(samples: train.Int16, record: ClipRecord) -> tuple[float, float] | None:
        located.append(record.prompt)
        return (0.5, 1.2)

    made = train.real_feats(run, real, lambda: locate)
    # Only the sentence prompt needed its words found.
    assert located == ["autobot, what time is it"]
    count = (4 + 4 + 21) * train.REAL_VARIANTS
    assert made.features.shape == (count, 28, 96)
    assert made.features.dtype == np.float16
    assert made.meta.shape == (count, 5) and made.source.shape == (count, 3)
    assert made.files == tuple(clip.record.file for clip in real.clips)
    # Window by window: nothing from a held-out clip, nothing from the last
    # fifth of an ambient recording.
    held_files = set(real.files(SPLIT_HELDOUT))
    for clip_index, first, last in made.source.tolist():
        clip = real.clips[clip_index]
        assert made.files[clip_index] not in held_files
        if clip.split == train.SPLIT_AMBIENT:
            assert 0 <= first and last <= clip.heldout[0]
    cache = work / "real" / f"{real.manifest}.npz"
    assert [path.name for path in (work / "real").iterdir()] == [cache.name]

    def never() -> train.Locator:
        raise AssertionError("a cached run does not transcribe again")

    again = train.real_feats(run, real, never)
    assert np.array_equal(again.features, made.features)
    assert np.array_equal(again.meta, made.meta)
    # A grown dataset gets a new cache and the old one goes.
    dataset.add(
        Clip(speech_like(0.6), RATE), record_for(LABEL_POSITIVE, "autobot", "", 400)
    )
    grown = train.load_real(dataset, "autobot")
    train.real_feats(run, grown, lambda: locate)
    assert [path.name for path in (work / "real").iterdir()] == [
        f"{grown.manifest}.npz"
    ]


def test_a_part_of_a_long_recording_is_read_without_the_rest(tmp_path: Path) -> None:
    audio = np.arange(5 * RATE, dtype=np.int64).astype(np.int16)
    dataset = VoiceDataset(tmp_path)
    stored = dataset.add(
        Clip(audio, RATE), record_for(LABEL_AMBIENT, "say nothing", "ambient-01", 1)
    )
    part = train.read_samples(dataset.path(stored), 2 * RATE, RATE)
    assert np.array_equal(part, audio[2 * RATE : 3 * RATE])
    # Past the end there is nothing, not an error.
    assert len(train.read_samples(dataset.path(stored), 5 * RATE, RATE)) == 0
    assert len(train.read_samples(dataset.path(stored), 4 * RATE + 8000, RATE)) == 8000


def test_a_clip_in_another_format_is_one_line(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path)
    dataset.add(
        Clip(room(1.0), 8000), record_for(LABEL_POSITIVE, "autobot", "bare-01", 1)
    )
    with pytest.raises(TrainError, match="not a 16000 Hz mono 16-bit WAV") as error:
        train.load_real(dataset, "autobot")
    assert "\n" not in str(error.value)
