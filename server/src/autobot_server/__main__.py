"""`python -m autobot_server`: run the inference server on `GAMEON1`.

Before it binds its port it checks, each with a one-line refusal and a
non-zero exit:

- the `[server]` table of `config/autobot.toml` (IS-3);
- that this host is `[server] hostname` (IS-25);
- the token file, `~/.config/autobot-server/token` at mode `0600` (IS-13);
- that the VRAM budget fits the card (IS-10).

Then it binds `0.0.0.0` on the port of `[server] url` (IS-2), loads and warms
its jobs in the background so that `/health` answers while they load, and
serves until it is stopped.

Speech-to-text runs faster-whisper from `[server] stt_model` (IS-8). Routing
and chat share the one language model `[server] llm_model`, served by the
runtime at `[server] runtime_url` (IS-9, IS-10); while no model is chosen, or
the runtime does not have it, they report not ready, with that reason, in
`/health`.
"""

from __future__ import annotations

import signal
import socket
import sys
import threading
from collections.abc import Iterator
from datetime import datetime
from types import FrameType

from autobot_server.app import App
from autobot_server.auth import TokenError, read_token, token_path
from autobot_server.chat import CHAT_PATH, chat_endpoint
from autobot_server.config import ServerConfigError, ServerSettings, load_settings
from autobot_server.contract import ContractError, contract_version
from autobot_server.gpu import GpuError, NvidiaSmi, check_budget
from autobot_server.hostcheck import WrongHostError, require_server_host
from autobot_server.http import serve
from autobot_server.job import Job, Readiness, keep_warm
from autobot_server.ollama import Ollama
from autobot_server.route import ROUTE_PATH, route_endpoint
from autobot_server.runtime import Answer, LanguageModel, Request, Runtime, Token
from autobot_server.stt import (
    STT_PATH,
    Transcribed,
    Utterance,
    WhisperJob,
    stt_endpoint,
)

EXIT_OK = 0
EXIT_TRANSIENT = 1
EXIT_PERMANENT = 2

# The line the log shows once the port is bound.
READY_MESSAGE = "serving"


def build_runtime(settings: ServerSettings) -> Runtime:
    """The runtime `[server] runtime` names; Ollama is the one there is (IS-10)."""
    return Ollama(settings.runtime_url, settings.llm_model, settings.llm_context)


def log(message: str) -> None:
    """One timestamped line on stdout, which is the server's log."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{stamp} server: {message}", flush=True)


def main() -> int:
    stop = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        settings = load_settings()
        require_server_host(socket.gethostname(), settings.hostname)
        token = read_token(token_path())
        gpu = NvidiaSmi()
        check_budget(gpu)
        version = contract_version()
    except (
        ContractError,
        GpuError,
        ServerConfigError,
        TokenError,
        WrongHostError,
    ) as error:
        print(error, file=sys.stderr, flush=True)
        return EXIT_PERMANENT
    whisper = WhisperJob.from_settings(settings)
    stt: Job[Utterance, Transcribed] = Job("stt", whisper)
    # One model behind both jobs, so that only one is resident (IS-10).
    llm = LanguageModel(build_runtime(settings))
    route: Job[Request, Answer | Iterator[Token]] = Job("route", llm)
    chat: Job[Request, Answer | Iterator[Token]] = Job("chat", llm)
    jobs: dict[str, Readiness] = {"stt": stt, "route": route, "chat": chat}
    app = App(
        token=token,
        jobs=jobs,
        gpu=gpu,
        contract_version=version,
        endpoints={
            STT_PATH: stt_endpoint(stt, whisper.model),
            ROUTE_PATH: route_endpoint(route),
            CHAT_PATH: chat_endpoint(chat, llm.model),
        },
        log=log,
    )
    try:
        server = serve(app, settings.port)
    except OSError as error:
        print(
            f"cannot bind port {settings.port}: {error.strerror}",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_TRANSIENT
    threading.Thread(
        target=keep_warm, args=(jobs.values(), stop), name="warm", daemon=True
    ).start()
    serving = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    serving.start()
    log(f"{READY_MESSAGE} on port {settings.port}")
    stop.wait()
    server.shutdown()
    server.server_close()
    log("stopped")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
