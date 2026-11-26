"""What the training clips say, and in which voices.

Everything here is a plain table. `phrase_table` picks the entries for one
wake phrase; a phrase without a table of its own gets a generic one, which
trains, but a table of words that sound like the phrase is what keeps false
accepts down, so a new phrase should be given one.

Hard-negative ids are positions in `PhraseTable.hard` and are stored in the
cached shards, so that tuple is append-only: add new phrases at the end.
"""

from __future__ import annotations

from dataclasses import dataclass

# voice -> relative share of the clips (voices with many speakers get more).
TRAIN_VOICES: dict[str, float] = {
    "en_US-libritts_r-medium": 30,
    "en_GB-vctk-medium": 18,
    "en_US-arctic-medium": 8,
    "en_GB-aru-medium": 5,
    "en_GB-semaine-medium": 3,
    "en_US-lessac-medium": 2.4,
    "en_US-amy-medium": 2.4,
    "en_US-ryan-medium": 2.4,
    "en_US-hfc_female-medium": 2.4,
    "en_US-hfc_male-medium": 2.4,
    "en_GB-alan-medium": 2.4,
    "en_GB-northern_english_male-medium": 2.4,
    "en_GB-jenny_dioco-medium": 2.4,
    "en_US-john-medium": 2.4,
    "en_US-norman-medium": 2.4,
    "en_US-bryce-medium": 2.4,
    "en_US-kusal-medium": 2.4,
    "en_US-hifi_tts_92-medium": 2.4,
    "en_US-ljspeech-medium": 2.4,
    "en_US-sam-medium": 2.4,
}
# Never used in training: the evaluation is on voices the model has not heard.
HELDOUT_VOICES: dict[str, float] = {
    "en_US-l2arctic-medium": 30,
    "en_US-kristin-medium": 12,
    "en_US-joe-medium": 12,
    "en_GB-alba-medium": 12,
    "en_GB-cori-medium": 12,
    "en_US-kathleen-low": 11,
    "en_US-danny-low": 11,
}

# Words before and after the phrase. The empty entries weight "no carrier".
PREFIXES: tuple[str, ...] = (
    *[""] * 9,
    *("hey", "hey", "hey", "yo", "yo", "okay", "ok", "hi", "hello", "so", "um"),
    *("oh", "excuse me", "good morning", "thanks", "now", "listen", "alright"),
    *("hey there", "well", "and", "right"),
)
SUFFIXES: tuple[str, ...] = (
    *[""] * 12,
    "what time is it",
    "turn on the lights",
    "turn off the kitchen lights",
    "what's the weather today",
    "set a timer for ten minutes",
    "play some music",
    "stop",
    "are you there",
    "can you hear me",
    "tell me a joke",
    "what's on my calendar",
    "remind me to call mom",
    "how are you",
    "good morning",
    "volume up",
    "pause",
    "next song",
    "add milk to the shopping list",
    "who won the game last night",
    "wake me up at seven",
    "is it going to rain",
    "what's the news",
    "thank you",
    "never mind",
    "open the garage",
    "lock the front door",
    "how far is the airport",
    "please",
    "I have a question",
    "start the vacuum",
)
# Names said where the phrase would be, in generic speech.
NAMES: tuple[str, ...] = (
    *("Robert", "Alexa", "Siri", "Google", "Jarvis", "computer", "Otto", "Abby"),
    *("buddy", "everybody", "Bob", "Albert", "Autumn", "Audrey", "Austin", "Arthur"),
    *("Toby", "doctor", "mister", "you"),
)
SENTENCES: tuple[str, ...] = (
    "I think we should leave before it gets dark",
    "the quick brown fox jumps over the lazy dog",
    "did you see the game last night",
    "I'll have a coffee with milk please",
    "she said the meeting moved to Tuesday",
    "we ought to go about it another way",
    "my car is at the auto shop getting new brakes",
    "the robot vacuum got stuck under the couch again",
    "that's a lot about nothing",
    "how much does that cost",
    "I bought a new bottle of olive oil",
    "they are out of butter at the store",
    "put it on the table over there",
    "what did you think about the movie",
    "he forgot about the appointment",
    "it's all about timing",
    "could you pass me the remote",
    "the weather has been awful lately",
    "let's order pizza tonight",
    "I talked to my brother yesterday",
    "the kids are playing in the yard",
    "this software update is automatic",
    "an autopilot keeps the plane level",
    "the bus was ten minutes late",
    "I need to charge my phone",
    "there is somebody at the door",
    "the dog wants to go out",
    "we watched a documentary about octopuses",
    "turn left at the next traffic light",
    "I ought to buy a new jacket",
    "the chatbot gave a strange answer",
    "one two three four five six seven",
    "nobody knows what happened",
    "that sounds like a good idea",
    "the bottom drawer is stuck",
    "are you coming to the party on Saturday",
    "it was a total disaster",
    "I can't remember where I parked",
    "she taught robotics at the college",
    "open a bottle of water",
    "the author signed my copy of the book",
    "what a beautiful autumn morning",
    "I got about eight hours of sleep",
    "we adopted a rabbit last spring",
    "the motorboat crossed the lake",
    "all of the above",
    "not at all",
    "oh that's not about that",
    "he's got a bot that trades stocks",
    "the auto industry is changing fast",
    "yes",
    "no",
    "okay",
    "maybe later",
    "thank you very much",
    "good night",
    "what",
    "hello there",
    "all right",
    "I thought about it a lot",
    "get out of the bath",
    "auto insurance is expensive",
    "a lot of traffic today",
    "the audio is too quiet",
    "audit the books by Friday",
    "the otter swam on its back",
    "bought a boat",
    "ought to be about eight o'clock",
    "the photo booth is broken",
    "about a block away",
    "hot water bottle",
)


@dataclass(frozen=True)
class PhraseTable:
    """The words the clips for one wake phrase are built from."""

    # How the phrase is written for the synthesiser; repeats weight a spelling.
    spellings: tuple[str, ...]
    # Words that sound like the phrase and must not fire. Append-only: a hard
    # negative's id is its position here.
    hard: tuple[str, ...]
    # Hard negatives kept out of training, so the evaluation has unseen ones.
    eval_only: frozenset[str]

    @property
    def trained(self) -> tuple[str, ...]:
        """The hard negatives the training clips use; repeats weight a phrase."""
        return tuple(text for text in self.hard if text not in self.eval_only)


# The first model's hard negatives, in the order its cached shards number them.
_AUTOBOT_HARD_V1: tuple[str, ...] = (
    *("auto", "robot", "bot", "a robot", "autopilot", "automatic", "automobile"),
    *("autograph", "autobahn", "auto body", "auto parts", "auto shop", "auto pay"),
    *("autofocus", "autocorrect", "autumn", "author", "otter", "order", "audit"),
    *("audible", "audio", "audiobook", "octopus", "chatbot", "about", "abbot"),
    *("rabbit", "adopt", "robotic", "ought to be", "a lot about", "all about"),
    *("out about", "on the bus", "odd about", "ought to buy", "motorboat"),
    *("autobus", "auto box", "auto bar", "photo booth", "auto", "robot", "automate"),
    *("auto repair", "otto", "awful lot", "all the bots", "a total bust"),
    *("outer banks", "ought to bet", "auto battery", "utter rot", "auto bill"),
    *("all too bad", "talk about", "thought about", "auto boot", "autoboard"),
    # Evaluation-only in the first model; the three it fired on most are
    # trained on since (see `_AUTOBOT_EVAL_ONLY`).
    *("autonomy", "auto loan", "ottoman", "robots", "not a bot", "auto tune"),
    *("oddball", "order book", "photo album", "otter pop", "water bottle"),
    *("auto body shop", "automated", "auto mode", "a lot of bots", "ought to bite"),
)
# Added after the first model: what it fired on, said more ways, and more
# phrases built like them (plurals, "bottle", "auto b...").
_AUTOBOT_HARD_V2: tuple[str, ...] = (
    *("a lot of bots", "lots of bots", "a lot of bots", "the bots", "two bots"),
    *("water bottle", "a bottle", "bottle", "bottles", "hot water bottle"),
    *("a lot of bottles", "odd about", "odd about it", "auto box", "auto box"),
    *("auto bar", "auto bar", "autobus", "autobus", "auto bus", "audible"),
    *("auto battery", "auto body", "otter box", "auto boat", "a lot of boats"),
    *("auto block", "auto ban", "auto bid", "auto back", "auto bike", "auto pot"),
    *("auto part", "auto dot", "auto bath", "auto bob", "auto bought", "photo book"),
    *("photo bomb", "robots", "a lot of robots", "ought to bought", "oughta but"),
    *("audio book", "audio bit", "order bot", "auto bought it", "all about that"),
    # Evaluation-only.
    *("auto bin", "auto bell", "bottle cap", "robot arm", "otter bite"),
    *("audiobooks", "a lot of bats", "auto bulb"),
)
_AUTOBOT_EVAL_ONLY = frozenset(
    {
        *("autonomy", "auto loan", "ottoman", "not a bot", "auto tune", "oddball"),
        *("order book", "photo album", "otter pop", "auto body shop", "automated"),
        *("auto mode", "ought to bite"),
        *("auto bin", "auto bell", "bottle cap", "robot arm", "otter bite"),
        *("audiobooks", "a lot of bats", "auto bulb"),
    }
)

PHRASE_TABLES: dict[str, PhraseTable] = {
    "autobot": PhraseTable(
        spellings=(*["autobot"] * 8, "Autobot", "auto bot"),
        hard=_AUTOBOT_HARD_V1 + _AUTOBOT_HARD_V2,
        eval_only=_AUTOBOT_EVAL_ONLY,
    ),
}

# Hard negatives for a phrase with no table: short everyday words and names.
GENERIC_HARD: tuple[str, ...] = (
    *("hello", "okay", "computer", "hey there", "all right", "excuse me"),
    *("thank you", "good morning", "what", "yes", "no", "maybe"),
)


def phrase_table(phrase: str) -> PhraseTable:
    """The table for `phrase`: its own, or a generic one built from its words."""
    key = " ".join(phrase.lower().split())
    if key in PHRASE_TABLES:
        return PHRASE_TABLES[key]
    words = key.split()
    # The words of a longer phrase, said alone, are its nearest misses.
    parts = tuple(words) if len(words) > 1 else ()
    return PhraseTable(
        spellings=(key,), hard=parts + GENERIC_HARD, eval_only=frozenset()
    )
