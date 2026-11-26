"""Wake-word engines: one module per wake phrase kind.

A module named after its kind exposes
`build(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector`;
`autobot.listener.detector.build_detector` finds it by name.
"""
