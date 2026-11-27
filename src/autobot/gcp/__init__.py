"""autobot's side of Google Cloud: what it needs there, and the checks that it has it.

autobot and its agents use Google Cloud only as their own service account,
never as the owner (GC-4). This package holds the logic of the Google Cloud
screen (`autobot.tui.screens.gcp`) and does no drawing:

- `needs`: the APIs, roles, permissions and secrets autobot needs, as data.
- `commands`: the `gcloud` commands that set those up, filled in from the
  configuration. They are text for the owner to run. Nothing here runs them.
- `keyfile`: whether the service account's key file is safe to use.
- `client`: the connection as the service account, and the one seam every
  check goes through (`GoogleCloud`).
- `checks`: the eight steps of the screen and the state of each.
- `secrets`: storing a pasted secret in Secret Manager and nowhere else.

Nothing here sends audio, a transcript for recognition or text for synthesis
anywhere (`CN-1`, `CN-2`).
"""
