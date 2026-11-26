"""Fetch the model files into `models/`: `python -m autobot.fetch_models`.

`make fetch-models` runs this. It is the one place in autobot that downloads
anything, and it is never run by the listener, the tests or the training:
those look files up under `models/` and stop with one line when one is
missing (CONSTRAINTS.md CN-1 and CN-2). What it fetches are public model and
data files; it sends nothing but the requests for them.

Without options it fetches what the listener and the tests need:

- openWakeWord's two feature models (`models/openwakeword/`),
- Silero VAD (`models/silero/`),
- faster-whisper `tiny.en` (`models/faster-whisper/tiny.en/`),
- the acknowledgement's piper voice (`models/piper/`).

The trained wake-word model itself is not fetched: `make train-wakeword`
builds it.

With `--training` it also fetches what `make train-wakeword` needs, about
6 GB with the default `--acav-gb 3` (the make target asks for more): 27 piper voices, openWakeWord's
validation features and the head of its general negative features, the MIT
impulse responses and the ESC-50 noise clips. `models/README.txt` lists them
with their licences.

Every download resumes: a partial file is kept as `<name>.part` and continued
with an HTTP Range request, and a file that is already there is not fetched
again. A larger `--acav-gb` extends the negative-features file it finds.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autobot.config import AckConfig, ConfigError, load_config, resolve_repo_path
from autobot.listener.models import MODELS_DIR
from autobot.wakeword.data import HELDOUT_VOICES, TRAIN_VOICES

HUGGING_FACE = "https://huggingface.co"
OPENWAKEWORD_RELEASE = (
    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
)
SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/"
    "silero_vad.onnx"
)
WHISPER_URL = f"{HUGGING_FACE}/Systran/faster-whisper-tiny.en/resolve/main"
WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
PIPER_URL = f"{HUGGING_FACE}/rhasspy/piper-voices/resolve/main"
FEATURES_URL = (
    f"{HUGGING_FACE}/datasets/davidscripka/openwakeword_features/resolve/main"
)
ACAV_NAME = "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
RIR_DATASET = "davidscripka/MIT_environmental_impulse_responses"
ESC50_URL = "https://github.com/karoldvl/ESC-50/archive/master.zip"

# The negative-features file: a 128-byte header, then float16 rows of [16, 96].
ACAV_HEADER_BYTES = 128
ACAV_ROW_BYTES = 16 * 96 * 2
DEFAULT_ACAV_GB = 3.0

CHUNK_BYTES = 1 << 20
PROGRESS_BYTES = 200 << 20
TRIES = 12
TIMEOUT_SECONDS = 60


class FetchError(Exception):
    """A download failed for good. The message is one line."""


@dataclass(frozen=True)
class Asset:
    """One file to fetch."""

    url: str
    # Relative to `models/`.
    path: Path
    # Fetch only this many bytes from the start of the file.
    limit: int | None = None


def piper_voice_url(voice: str) -> str:
    """The URL of a piper voice's `.onnx`: "en_US-lessac-medium" -> .../en/en_US/..."""
    try:
        language, name, quality = voice.split("-")
    except ValueError:
        raise FetchError(
            f"piper voice {voice!r} is not named <language>-<name>-<quality>"
        ) from None
    family = language.split("_")[0]
    return f"{PIPER_URL}/{family}/{language}/{name}/{quality}/{voice}.onnx"


def voice_assets(voice: str) -> list[Asset]:
    url = piper_voice_url(voice)
    path = Path("piper") / f"{voice}.onnx"
    return [Asset(url, path), Asset(url + ".json", Path(f"{path}.json"))]


def acav_bytes(gigabytes: float) -> int:
    """The byte count for `gigabytes` of negative features: header plus whole rows."""
    rows = int(gigabytes * 1e9) // ACAV_ROW_BYTES
    return ACAV_HEADER_BYTES + rows * ACAV_ROW_BYTES


def runtime_assets(ack_voice: str) -> list[Asset]:
    """What the listener and the tests need."""
    assets = [
        Asset(f"{OPENWAKEWORD_RELEASE}/{name}", Path("openwakeword") / name)
        for name in ("melspectrogram.onnx", "embedding_model.onnx")
    ]
    assets.append(Asset(SILERO_VAD_URL, Path("silero") / "silero_vad.onnx"))
    assets += [
        Asset(f"{WHISPER_URL}/{name}", Path("faster-whisper") / "tiny.en" / name)
        for name in WHISPER_FILES
    ]
    return assets + voice_assets(ack_voice)


def training_assets(acav_gigabytes: float) -> list[Asset]:
    """The single files the training needs; the two archives are fetched apart."""
    assets: list[Asset] = []
    for voice in (*TRAIN_VOICES, *HELDOUT_VOICES):
        assets += voice_assets(voice)
    features = Path("wakeword") / "data" / "features"
    assets.append(
        Asset(
            f"{FEATURES_URL}/validation_set_features.npy",
            features / "validation_set_features.npy",
        )
    )
    assets.append(
        Asset(
            f"{FEATURES_URL}/{ACAV_NAME}",
            features / "acav100m_head.npy.raw",
            limit=acav_bytes(acav_gigabytes),
        )
    )
    return assets


def say(message: str) -> None:
    print(message, flush=True)


def _open(url: str, first_byte: int, limit: int | None) -> Any:
    request = urllib.request.Request(url)
    if first_byte or limit is not None:
        last = "" if limit is None else str(limit - 1)
        request.add_header("Range", f"bytes={first_byte}-{last}")
    return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)


def fetch_once(url: str, destination: Path, limit: int | None = None) -> bool:
    """Download `url` to `destination`, resuming a partial file.

    Returns False when the file was already there. With `limit`, only the
    first `limit` bytes are wanted, and a shorter file already there is
    extended.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    if destination.exists():
        if limit is None or destination.stat().st_size >= limit:
            return False
        # Shorter than what is wanted now: carry on from where it ends.
        destination.rename(part)
    have = part.stat().st_size if part.exists() else 0
    if limit is not None and have >= limit:
        part.rename(destination)
        return True
    try:
        response = _open(url, have, limit)
    except urllib.error.HTTPError as error:
        if error.code == 416 and have:
            # Nothing after what we have: it was complete.
            part.rename(destination)
            return True
        raise
    with response:
        if have and response.status != 206:
            # The server sent the whole file again.
            have = 0
            part.unlink()
        total = have + int(response.headers.get("Content-Length") or 0)
        done = reported = have
        with part.open("ab") as handle:
            while chunk := response.read(CHUNK_BYTES):
                handle.write(chunk)
                done += len(chunk)
                if done - reported >= PROGRESS_BYTES:
                    reported = done
                    say(f"  {destination.name}: {done >> 20}/{total >> 20} MB")
    if total and done < total:
        raise OSError(f"connection closed after {done} of {total} bytes")
    part.rename(destination)
    say(f"fetched {destination} ({done >> 20} MB)")
    return True


def fetch(url: str, destination: Path, limit: int | None = None) -> bool:
    """`fetch_once`, retried; every retry resumes the partial file."""
    for attempt in range(1, TRIES + 1):
        try:
            return fetch_once(url, destination, limit)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404):
                raise FetchError(f"cannot fetch {url}: HTTP {error.code}") from None
            reason = f"HTTP {error.code}"
        except (urllib.error.URLError, OSError) as error:
            reason = type(error).__name__
        say(f"  retry {attempt}/{TRIES} of {destination.name}: {reason}")
        time.sleep(min(60, 3 * attempt))
    raise FetchError(f"cannot fetch {url}: gave up after {TRIES} tries")


def fetch_impulse_responses(models: Path) -> None:
    """The MIT impulse responses: one small WAV per room."""
    directory = models / "wakeword" / "data" / "rir"
    stamp = directory / ".complete"
    if stamp.exists():
        return
    listing = f"{HUGGING_FACE}/api/datasets/{RIR_DATASET}"
    try:
        with urllib.request.urlopen(listing, timeout=TIMEOUT_SECONDS) as response:
            files = [entry["rfilename"] for entry in json.load(response)["siblings"]]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
        raise FetchError(f"cannot list {listing}: {type(error).__name__}") from None
    for name in files:
        if name.endswith(".wav"):
            fetch(
                f"{HUGGING_FACE}/datasets/{RIR_DATASET}/resolve/main/{name}",
                directory / Path(name).name,
            )
    stamp.write_text("")


def fetch_noise(models: Path) -> None:
    """ESC-50: the `audio/` folder of the archive, and nothing else from it."""
    directory = models / "wakeword" / "data" / "noise"
    out = directory / "esc50"
    if out.exists():
        return
    archive = directory / "ESC-50-master.zip"
    fetch(ESC50_URL, archive)
    unpacking = directory / "esc50.tmp"
    shutil.rmtree(unpacking, ignore_errors=True)
    unpacking.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.filename.endswith(".wav") and "/audio/" in member.filename:
                # Only the base name is used: a member cannot write elsewhere.
                (unpacking / Path(member.filename).name).write_bytes(
                    bundle.read(member)
                )
    unpacking.rename(out)
    archive.unlink()
    say(f"fetched {out}")


def ack_voice() -> str:
    """The configured acknowledgement voice; the default when there is no config."""
    try:
        return load_config().ack.voice
    except ConfigError:
        return AckConfig.voice


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autobot.fetch_models",
        description="Fetch the model files into models/. Resumable.",
    )
    parser.add_argument(
        "--training",
        action="store_true",
        help="also fetch what 'make train-wakeword' needs (several GB)",
    )
    parser.add_argument(
        "--acav-gb",
        type=float,
        default=DEFAULT_ACAV_GB,
        help="gigabytes of openWakeWord's general negative features to fetch, "
        "of 17 (with --training)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    models = resolve_repo_path(MODELS_DIR)
    try:
        assets = runtime_assets(ack_voice())
        if args.training:
            assets += training_assets(args.acav_gb)
        fetched = sum(
            fetch(asset.url, models / asset.path, asset.limit) for asset in assets
        )
        if args.training:
            fetch_impulse_responses(models)
            fetch_noise(models)
    except FetchError as error:
        print(error, file=sys.stderr, flush=True)
        return 1
    what = "the listener's and the training's" if args.training else "the listener's"
    say(
        f"{what} model files are in {models} "
        f"({fetched} fetched now, {len(assets) - fetched} already there)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
