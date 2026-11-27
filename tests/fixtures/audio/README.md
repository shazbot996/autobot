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

Every detector kind is held to this table: the `text_match`, `openwakeword`
and `sherpa_kws` tests each run all six clips through the service's frame path.

**These are placeholders, not recordings.** Nobody was available to speak when
they were made, so the speech clips are synthesised locally with piper-tts
(voices `ryan`, `amy`, `hfc_male`, `kristin` and `joe`, none of them the
acknowledgement's voice) and the room noise is generated. Each has silence
before and after and low added noise. `synthesise.py` in this directory made
them; piper's synthesis is not deterministic, so running it again gives
slightly different clips, and the tests must be re-run after it.

Replace them with real recordings, one clip at a time, from the Dataset screen
of the terminal user interface (`make launch`). Recordings are made into the
voice dataset (`data/voice/`), which is never committed; a fixture is a
deliberate export of one of them. On the Dataset screen choose a clip, press
`E`, pick one of the file names above and confirm. The export keeps the file
name and gives the speech about half a second of quiet before it and a full
second after it, adding silence where the recording has less, so the tests
keep passing unchanged. Nothing else writes into this directory: a fixture is
committed, and so public, and what goes into it is chosen clip by clip.
