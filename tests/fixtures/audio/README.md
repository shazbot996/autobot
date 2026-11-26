# Audio fixtures

16 kHz mono 16-bit WAV clips that the listener tests feed through the same
`feed()` path the service uses (`tests/listener/`). No test opens a
microphone, a speaker or the network.

| File | Says | Expected |
|---|---|---|
| `autobot.wav` | "Autobot." | one `WakeEvent` |
| `hey_autobot.wav` | "Hey, autobot." | one `WakeEvent` |
| `yo_autobot.wav` | "Yo, autobot." | one `WakeEvent` |
| `room_noise.wav` | nothing: rumble, hum, hiss, two clicks | none |
| `unrelated_speech.wav` | "The weather tomorrow looks fine for a walk in the park." | none |
| `near_miss_auto.wav` | "Auto." | none |

**These are placeholders, not recordings.** Nobody was available to speak when
they were made, so the speech clips are synthesised locally with piper-tts
(voices `ryan`, `amy`, `hfc_male`, `kristin` and `joe`, none of them the
acknowledgement's voice) and the room noise is generated. Each has silence
before and after and low added noise. `synthesise.py` in this directory made
them; piper's synthesis is not deterministic, so running it again gives
slightly different clips, and the tests must be re-run after it.

Replace them with real recordings made on the NUC from the Audio screen of the
terminal user interface (`make deploy`), which saves clips into this
directory. Keep the file names, and keep about half a second of quiet at the
start and a full second at the end of each clip, so the tests keep passing
unchanged.
