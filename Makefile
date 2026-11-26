# autobot runtime control layer (AGENTS.md, "Runtime control and audio checks").
#
# A bare `make` prints the summary. Every target carries a `## ` comment on its
# rule line; the `help` target builds the summary from those comments, so a new
# target appears in the summary as soon as it has one.
#
# Each target lives in its own block between `# ---` rules. A plan step that
# builds a stubbed target replaces only that target's block.

.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV   := .venv
PYTHON := $(VENV)/bin/python
PLAN   := docs/plans/active/headless-wake-word-prototype-PLAN.md

# $(call not_built,N): the body of a target whose plan step N has not landed.
define not_built
	@echo "not built yet: see $(PLAN) step $(1)" >&2; exit 1
endef

# Fails with one line when `make setup` has not been run.
.PHONY: venv-check
venv-check:
	@test -x $(PYTHON) || { echo "no $(VENV): run 'make setup' first" >&2; exit 1; }

# --- help -------------------------------------------------------------------
.PHONY: help
help: ## Print this summary of the available commands
	@echo "autobot: make targets"
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F ':.*## ' '{ printf "  %-15s %s\n", $$1, $$2 }'
	@echo "No front-end targets exist: this build is headless."

# --- setup ------------------------------------------------------------------
.PHONY: setup
setup: ## Create .venv and install autobot with its dev extras
	@test -x $(PYTHON) || python3 -m venv $(VENV)
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet --editable '.[dev]'

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

# --- deploy -----------------------------------------------------------------
.PHONY: deploy
deploy: venv-check ## Open the terminal user interface: audio, wake phrase, service, host
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
# Trains models/wakeword/<phrase>.onnx on the CPU. It takes hours and is
# resumable: run it again and it carries on. Variables:
#   SCALE    how much speech to synthesise (1 is about 23,000 clips and takes
#            about 25 minutes; a larger SCALE keeps what a smaller one made)
#   STEPS    training steps
#   THREADS  worker processes
#   ACAV_GB  gigabytes of openWakeWord's negative features to fetch and use
#   PHRASE   the wake phrase (default: the first one in config/autobot.toml)
#   TRAIN_ARGS  anything else for `python -m autobot.wakeword.train`
# The first run installs the `train` extra into .venv (the CPU build of torch)
# and fetches the training data; after that nothing is downloaded.
SCALE   ?= 10
STEPS   ?= 50000
THREADS ?= 8
TORCH_INDEX := https://download.pytorch.org/whl/cpu
.PHONY: train-wakeword
train-wakeword: venv-check ## Train the wake-word model; hours, resumable (SCALE=10 STEPS=50000 THREADS=8 ACAV_GB=12)
	@$(PYTHON) -c "import onnx, scipy, soundfile, torch" 2>/dev/null || { \
		echo "installing the training extras into $(VENV) (CPU torch)"; \
		$(PYTHON) -m pip install --quiet --index-url $(TORCH_INDEX) torch \
		&& $(PYTHON) -m pip install --quiet --editable '.[train]'; }
	@$(PYTHON) -m autobot.fetch_models --training --acav-gb $(ACAV_GB)
	@$(PYTHON) -m autobot.wakeword.train --scale $(SCALE) --steps $(STEPS) \
		--threads $(THREADS) $(if $(PHRASE),--phrase "$(PHRASE)") $(TRAIN_ARGS)
