"""`make server-fetch-models`: the one downloader on `GAMEON1` (IS-7, IS-25).

    python -m autobot_server.fetch_models

It fetches the speech-to-text model, the CTranslate2 files of faster-whisper
`large-v3-turbo`, into the directory `[server] stt_model` names under the
checkout's `models/`. Nothing else on this host downloads: the server loads
its models from local files only and never fetches at run time.

The language model is Ollama's to hold. This command does not pull it: it
prints the `ollama pull` for `[server] llm_model`, or for the candidates of
IS-10 while none is chosen, and the owner runs it.

A file that is already there is not fetched again; a partial one is kept as
`<name>.part` and continued with an HTTP Range request. What is sent is
only the requests for the public model files.
"""

from __future__ import annotations

import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from autobot_server.config import ServerConfigError, ServerSettings, load_settings
from autobot_server.hostcheck import WrongHostError, require_server_host

# The CTranslate2 conversion faster-whisper itself names for `large-v3-turbo`.
WHISPER_URL = (
    "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main"
)
WHISPER_FILES = (
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
    "model.bin",
)
# The model the URL above is: another `stt_model` is not something to fetch from it.
WHISPER_NAME = "large-v3-turbo"
# The candidates to measure first (IS-10, IS-26): the two whose Ollama names
# are settled, and the third, whose tag the owner picks from Ollama's library.
LLM_CANDIDATES = ("qwen3:8b", "llama3.1:8b")
LLM_CANDIDATE_TO_PICK = "a Gemma 4 that fits in about 5 GB at Q4_K_M"

CHUNK_BYTES = 1 << 20
PROGRESS_BYTES = 200 << 20
TRIES = 8
TIMEOUT_SECONDS = 60


class FetchError(Exception):
    """A download failed for good. The message is one line."""


def fetch(url: str, target: Path, log: Callable[[str], None] = print) -> bool:
    """Download `url` to `target`, resuming a partial file; false when it was there."""
    if target.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(1, TRIES + 1):
        have = partial.stat().st_size if partial.is_file() else 0
        request = urllib.request.Request(
            url, headers={"Range": f"bytes={have}-"} if have else {}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
                # A server that ignores the Range header sends the whole file.
                mode = "ab" if have and reply.status == 206 else "wb"
                reported = have if mode == "ab" else 0
                with partial.open(mode) as handle:
                    while chunk := reply.read(CHUNK_BYTES):
                        handle.write(chunk)
                        if handle.tell() - reported >= PROGRESS_BYTES:
                            reported = handle.tell()
                            log(f"  {target.name}: {reported >> 20} MiB")
            partial.replace(target)
            return True
        except urllib.error.HTTPError as error:
            if error.code == 416 and have:
                # The partial file is already the whole file.
                partial.replace(target)
                return True
            if error.code in (401, 403, 404):
                raise FetchError(
                    f"cannot fetch {url}: {error.code} {error.reason}"
                ) from None
            reason = f"{error.code} {error.reason}"
        except OSError as error:
            reason = str(getattr(error, "reason", None) or error.strerror or error)
        if attempt < TRIES:
            log(f"  {target.name}: {reason}; trying again ({attempt}/{TRIES})")
            time.sleep(min(30, 2**attempt))
    raise FetchError(f"cannot fetch {url}: {reason}")


def fetch_whisper(settings: ServerSettings, log: Callable[[str], None] = print) -> None:
    """The speech-to-text model's files into `[server] stt_model`."""
    directory = settings.stt_model_path
    if settings.stt_model_name != WHISPER_NAME:
        raise FetchError(
            f"[server] stt_model names {settings.stt_model_name}, and only "
            f"{WHISPER_NAME} is fetched here: put its CTranslate2 files in "
            f"{directory} by hand"
        )
    for name in WHISPER_FILES:
        fresh = fetch(f"{WHISPER_URL}/{name}", directory / name, log)
        log(f"{'fetched' if fresh else 'present'}: {directory / name}")


def pull_lines(settings: ServerSettings) -> list[str]:
    """What to tell the owner about the language model: the pull is his to run."""
    if settings.llm_model:
        return [
            "the language model is Ollama's to fetch; run this yourself:",
            f"  ollama pull {settings.llm_model}",
        ]
    return [
        "no language model is chosen yet ([server] llm_model is empty).",
        "pull the candidates yourself, then measure each with 'make server-eval':",
        *(f"  ollama pull {name}" for name in LLM_CANDIDATES),
        f"  and {LLM_CANDIDATE_TO_PICK}, by the tag Ollama's library gives it",
    ]


def main(hostname: Callable[[], str] = socket.gethostname) -> int:
    try:
        settings = load_settings()
        require_server_host(hostname(), settings.hostname)
        fetch_whisper(settings)
    except (FetchError, ServerConfigError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1
    for line in pull_lines(settings):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
