# Build the Google Cloud configuration screen plan: what was done

Written outside cr2, in Claude Code, by the model claude-fable-5-2, on 2026-11-27.

All seven steps of `docs/plans/active/google-cloud-configuration-screen-PLAN.md` are built and stamped `completed` in the plan's frontmatter.

## The checks

- `make typecheck` passes.
- `node .agents/skills/cr2-conformance/scripts/check.mjs` passes, with two warnings about section headings that it gave before any change.
- `make test`: 1075 pass and 1 fails. The failure is `tests/test_config.py::test_the_committed_config_is_a_fresh_install`, which wants `[tui] theme = "light"` while the committed `config/autobot.toml` says `"sun"`. It failed before any change and was left alone, because the theme is the owner's setting.
- `make lint` fails on 250 findings, all of them in the untracked `assistant/` directory, which was there before any change. `ruff check` and `ruff format --check` pass on everything else.

Each step's "Done when" names those three targets. The steps are stamped `completed` because everything the plan builds passes, and the two failures were there before the run and are not the plan's to fix.

## What differs from the plan

- **A seventh role.** `autobot.gcp.needs` lists `roles/cloudquotas.viewer` on the project besides the six roles of GC-6. The plan reads a quota's live limit by its quota id, which goes through the Cloud Quotas API, and none of the six roles holds `cloudquotas.quotas.get`. The spec's GC-6 table does not list the role.
- **The quota ids are guesses.** The three seeded `[[gcp.quotas]]` ids were not checked against the project, and Google publishes none for the Weather API. A wrong id shows as *missing* on the Rate limits step, with the command that lists the real ones.
- **A command longer than the screen ends each row in a backslash**, so the rows pasted together are one command. The plan says only that it continues onto the next line.
- **An open step has no side borders** (`View.framed` in `src/autobot/tui/app.py`), so nothing stands beside a command.
- **`pythonpath = ["tests"]`** was added to pytest's settings so `tests/tui/` can import the fake of `tests/gcp/`.
- **`tests/tui/test_tui_routing_screen.py` and `tests/tui/test_tui_app.py`** were changed for the thirteenth menu entry, and the main menu leaves out the rule above its state panel in a terminal of the minimum height. Neither file is in the plan's list.

## Not done, as the plan says

The context documents, the other specs and the new ADR that the spec's "Changes needed elsewhere" lists are untouched. `SCHEMA.md`, `ARCH.md`, `GLOSSARY.md` and `INFRASTRUCTURE.md` are descriptive and now trail the code.

Nothing was run against the real Google Cloud project: no key exists, and no `gcloud` command was run with the owner's login.
