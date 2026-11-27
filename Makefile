# autobot runtime control layer (AGENTS.md, "Runtime control and audio checks").
#
# A bare `make` prints the summary. Every target carries a `## ` comment on its
# rule line; the `help` target builds the summary from those comments, so a new
# target appears in the summary as soon as it has one.
#
# Each target lives in its own block between `# ---` rules. Every target here
# is built: none is a stub.

.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV   := .venv
PYTHON := $(VENV)/bin/python

# The inference server's own environment, on GAMEON1 only (IS-25).
SERVER_VENV   := .venv-server
SERVER_PYTHON := $(SERVER_VENV)/bin/python

# Fails with one line when `make setup` has not been run.
.PHONY: venv-check
venv-check:
	@test -x $(PYTHON) || { echo "no $(VENV): run 'make setup' first" >&2; exit 1; }

# Fails with one line off GAMEON1, and when `make server-setup` has not been run.
.PHONY: server-venv-check
server-venv-check:
	@PYTHONPATH=server/src python3 -m autobot_server.hostcheck
	@test -x $(SERVER_PYTHON) || { echo "no $(SERVER_VENV): run 'make server-setup' first" >&2; exit 1; }

# --- help -------------------------------------------------------------------
.PHONY: help
help: ## Print this summary of the available commands
	@echo "autobot: make targets"
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F ':.*## ' '{ printf "  %-20s %s\n", $$1, $$2 }'
	@echo "\`make launch\` opens the application: the full-screen terminal user interface."

# --- setup ------------------------------------------------------------------
.PHONY: setup
setup: ## Create .venv and install autobot with its dev extras
	@test -x $(PYTHON) || python3 -m venv $(VENV)
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet --editable '.[dev]'
	# The inference server's package, without its `gpu` extra: on the NUC it
	# is only linted, type-checked and tested, on fakes (IS-12).
	$(PYTHON) -m pip install --quiet --editable ./server

# --- test -------------------------------------------------------------------
.PHONY: test
test: venv-check ## Run the tests (pytest; no microphone, speaker or network)
	$(PYTHON) -m pytest

# --- lint -------------------------------------------------------------------
.PHONY: lint
lint: venv-check ## Check linting and formatting (ruff)
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

# --- typecheck --------------------------------------------------------------
.PHONY: typecheck
typecheck: venv-check ## Run the static type checker (mypy)
	$(PYTHON) -m mypy

# --- up ---------------------------------------------------------------------
.PHONY: up
up: venv-check ## Back end: start the listener (the systemd unit when it is installed)
	@$(PYTHON) -m autobot.listener.control up

# --- backend-up -------------------------------------------------------------
.PHONY: backend-up
backend-up: up ## Back end: alias of `up`

# --- down -------------------------------------------------------------------
.PHONY: down
down: venv-check ## Back end: stop the listener
	@$(PYTHON) -m autobot.listener.control down

# --- backend-down -----------------------------------------------------------
.PHONY: backend-down
backend-down: down ## Back end: alias of `down`

# --- status -----------------------------------------------------------------
.PHONY: status
status: venv-check ## Show whether the listener runs, with uptime, CPU and memory
	@$(PYTHON) -m autobot.listener.control status

# --- logs -------------------------------------------------------------------
# `make logs` prints the end of run/listener.log; `make logs FOLLOW=1` follows it.
.PHONY: logs
logs: venv-check ## Print the end of the listener log (FOLLOW=1 keeps following it)
	@$(PYTHON) -m autobot.listener.control logs $(if $(FOLLOW),--follow)

# --- audio-check ------------------------------------------------------------
.PHONY: audio-check
audio-check: venv-check ## Check the NUC's audio hardware: list devices, record 2 s, play it back (NUC only)
	@$(PYTHON) -m autobot.audio_check

# --- launch -----------------------------------------------------------------
.PHONY: launch
launch: venv-check ## Open the application: the wake-word training studio and the settings screens
	@$(PYTHON) -m autobot.tui

# --- fetch-models -----------------------------------------------------------
# The only target that downloads. `make fetch-models` fetches what the listener
# and the tests need; `make fetch-models TRAIN=1` adds the voices and data sets
# `make train-wakeword` needs (ACAV_GB gigabytes of openWakeWord's negative
# features, of 17, and about 3 GB besides). Resumable.
ACAV_GB ?= 12
.PHONY: fetch-models
fetch-models: venv-check ## Fetch the model files into models/ (TRAIN=1 adds the training voices and data)
	@$(PYTHON) -m autobot.fetch_models $(if $(TRAIN),--training --acav-gb $(ACAV_GB))

# --- train-wakeword ---------------------------------------------------------
# Trains the wake-word model on the CPU and writes it as a candidate
# (models/wakeword/candidate.onnx and candidate.json), which the interface
# (`make launch`) compares and installs: the installed model and
# config/autobot.toml are never touched here. The recordings in data/voice/ are
# trained on beside the synthetic speech, with the held-out share kept for the
# evaluation; with no recordings it trains on synthetic speech alone. The first
# run takes hours and is resumable: run it again and it carries on. Once the
# synthetic speech is cached a run takes about half an hour. Variables:
#   SCALE    how much speech to synthesise (1 is about 23,000 clips and takes
#            about 25 minutes; a larger SCALE keeps what a smaller one made)
#   STEPS    training steps
#   THREADS  worker processes
#   ACAV_GB  gigabytes of openWakeWord's negative features to fetch and use
#   PHRASE   the wake phrase (default: the first one in config/autobot.toml)
#   TRAIN_ARGS  anything else for `python -m autobot.wakeword.train`, such as
#            --no-exclude-near-homophones (train on the sound-alikes that are
#            left out by default) or --dataset DIR (another voice dataset)
# The first run installs the `train` extra into .venv (the CPU build of torch)
# and fetches the training data; after that nothing is downloaded.
SCALE   ?= 10
STEPS   ?= 50000
THREADS ?= 8
TORCH_INDEX := https://download.pytorch.org/whl/cpu
.PHONY: train-wakeword
train-wakeword: venv-check ## Train a wake-word candidate for the interface to install; hours, resumable (SCALE=10 STEPS=50000 THREADS=8 ACAV_GB=12)
	@$(PYTHON) -c "import onnx, scipy, soundfile, torch" 2>/dev/null || { \
		echo "installing the training extras into $(VENV) (CPU torch)"; \
		$(PYTHON) -m pip install --quiet --index-url $(TORCH_INDEX) torch \
		&& $(PYTHON) -m pip install --quiet --editable '.[train]'; }
	@$(PYTHON) -m autobot.fetch_models --training --acav-gb $(ACAV_GB)
	@$(PYTHON) -m autobot.wakeword.train --scale $(SCALE) --steps $(STEPS) \
		--threads $(THREADS) $(if $(PHRASE),--phrase "$(PHRASE)") $(TRAIN_ARGS)

# --- the inference server (GAMEON1) -------------------------------------------
# The targets below run on GAMEON1, the GPU inference server, inside its WSL
# distribution; each refuses on any other host (IS-25). `server-ping` is the
# NUC's, and `server-token` runs on both. The Windows, router and `apt` steps
# that come first are the owner's: docs/context/INFRASTRUCTURE.md lists them.

# --- server-setup -----------------------------------------------------------
.PHONY: server-setup
server-setup: ## GAMEON1: create .venv-server and install the server with its GPU extras
	@PYTHONPATH=server/src python3 -m autobot_server.hostcheck
	@test -x $(SERVER_PYTHON) || python3 -m venv $(SERVER_VENV)
	$(SERVER_PYTHON) -m pip install --quiet --upgrade pip
	$(SERVER_PYTHON) -m pip install --quiet --editable './server[gpu]'

# --- server-fetch-models ----------------------------------------------------
# The only target that downloads on GAMEON1. It fetches the speech-to-text
# model and prints the `ollama pull` for the language model, which the owner
# runs himself.
.PHONY: server-fetch-models
server-fetch-models: server-venv-check ## GAMEON1: fetch the speech-to-text model into models/; print the ollama pull to run
	@$(SERVER_PYTHON) -m autobot_server.fetch_models

# --- server-up --------------------------------------------------------------
.PHONY: server-up
server-up: server-venv-check ## GAMEON1: start the inference server (the systemd unit when it is installed)
	@$(SERVER_PYTHON) -m autobot_server.control up

# --- server-down ------------------------------------------------------------
.PHONY: server-down
server-down: server-venv-check ## GAMEON1: stop the inference server
	@$(SERVER_PYTHON) -m autobot_server.control down

# --- server-status ----------------------------------------------------------
.PHONY: server-status
server-status: server-venv-check ## GAMEON1: show whether the server runs and which jobs are ready
	@$(SERVER_PYTHON) -m autobot_server.control status

# --- server-logs ------------------------------------------------------------
# `make server-logs` prints the end of run/server.log; FOLLOW=1 follows it.
.PHONY: server-logs
server-logs: server-venv-check ## GAMEON1: print the end of the server log (FOLLOW=1 keeps following it)
	@$(SERVER_PYTHON) -m autobot_server.control logs $(if $(FOLLOW),--follow)

# --- server-install ---------------------------------------------------------
.PHONY: server-install
server-install: server-venv-check ## GAMEON1: install the server's systemd user unit, enable it with lingering, start it
	@$(SERVER_PYTHON) -m autobot_server.control install

# --- server-check -----------------------------------------------------------
.PHONY: server-check
server-check: server-venv-check ## GAMEON1: loopback smoke test of /health, /v1/stt, /v1/route and /v1/chat
	@$(SERVER_PYTHON) -m autobot_server.check

# --- server-eval ------------------------------------------------------------
# Measures one language model against the real runtime and writes
# run/server-eval-<model>.json: route accuracy, JSON validity and latency
# (IS-26). MODEL= names the candidate; the default is [server] llm_model.
.PHONY: server-eval
server-eval: server-venv-check ## GAMEON1: measure a language model and write run/server-eval-<model>.json (MODEL=name)
	@$(SERVER_PYTHON) -m autobot_server.eval $(if $(MODEL),--model "$(MODEL)")

# --- server-token -----------------------------------------------------------
# The shared bearer token (IS-13). On GAMEON1 it makes the token file, when
# there is none, and prints its path. On the NUC it asks for the token at a
# hidden prompt and writes secrets/server-token. Both files are mode 0600 and
# the token is never printed. The owner runs this himself, on each host, and
# carries the value between them; nothing copies it over the LAN.
.PHONY: server-token
server-token: ## Make the server's token (on GAMEON1) or enter it at a hidden prompt (on the NUC)
	@if PYTHONPATH=server/src python3 -m autobot_server.hostcheck 2>/dev/null; then \
		PYTHONPATH=server/src python3 -m autobot_server.token; \
	else \
		test -x $(PYTHON) || { echo "no $(VENV): run 'make setup' first" >&2; exit 1; }; \
		$(PYTHON) -m autobot.server.token; \
	fi

# --- server-ping ------------------------------------------------------------
.PHONY: server-ping
server-ping: venv-check ## NUC: ask the inference server's /health once and print its state
	@$(PYTHON) -m autobot.server.ping
