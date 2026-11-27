"""The `weather` agent: the weather for the owner's zip code (AG-6, AG-7). No drawing.

The command `weather` gives today; the mode `forecast` gives the days
ahead. Each step runs in `tick`, which never blocks:

1. **Checks.** No zip code is one *trouble* line naming the Agent editor.
2. **The key.** The API key is read from Secret Manager, as autobot's
   service account, on a thread (`Seams.connect`, then `secret_value`). A
   missing key file, a refused identity or an empty secret is one *trouble*
   line (AG-12). The key is held in a buffer that is cleared after the call
   and is never written anywhere.
3. **The confirmation.** With `confirm_request` on, the definition's
   question is asked first (AG-13): anything but a yes ends the agent with
   nothing looked up.
4. **The rate.** The agent counts its own calls to the API against the
   definition's two limits (`weather_api.RateControl`): two for today's
   weather, one for the days ahead. A call past either limit is refused in
   one line. Google's cap on the API is the backstop behind this (GC-10).
5. **The lookup.** It goes out on a thread, with the eyes *working*
   (`Seams.weather`, Google's Weather API in the interface).
6. **The answer.** In two parts (AG-4): a spoken sentence, and the day, or
   a line per day, in the detail panel.

Only the zip code's coordinates and the key reach Google; no audio and no
transcript does (`CN-1`).
"""

from __future__ import annotations

from typing import Literal

from autobot.agents.base import Launch
from autobot.agents.confirm import Confirmation
from autobot.agents.response import Response
from autobot.agents.speech import Reply
from autobot.agents.weather_api import Forecast, RateControl
from autobot.config import MODE_FORECAST
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import FunctionTask

type Stage = Literal["key", "confirming", "lookup", "answer", "done"]

# Where the moments of the agent's calls are kept: runtime state of this
# machine, not agent memory.
CALLS_FILE = "run/weather-calls.json"
# How many calls to the Weather API one lookup makes (`weather_api.GoogleWeather`).
TODAY_CALLS = 2
DAYS_CALLS = 1
NO_ZIP_CODE = "the weather agent has no zip code: set it in the Agent editor"
NO_CLOUD = "the weather agent cannot reach Google Cloud from here"
EMPTY_SECRET = "the secret {secret} is empty: paste the key in the Agent editor"
GETTING = "Getting the weather for {zip_code}."
GETTING_DAYS = "Getting the forecast for {zip_code}."
NOT_CONFIRMED = "Not confirmed: nothing was looked up."
CANCELLED = "the weather lookup was cancelled"


def _clear(key: bytearray) -> None:
    key[:] = bytes(len(key))


def today_response(forecast: Forecast) -> Response:
    """Today in two parts: one spoken sentence, and the day laid out."""
    summary = (
        f"It's {forecast.temperature} degrees and {forecast.condition.lower()}, "
        f"with a high of {forecast.high}, a low of {forecast.low} and a "
        f"{forecast.precipitation} percent chance of rain."
    )
    detail = (
        f"Weather for {forecast.place}",
        (
            f"Now       {forecast.condition}, {forecast.temperature}°F "
            f"(feels like {forecast.feels_like}°F)"
        ),
        f"Today     high {forecast.high}°F, low {forecast.low}°F",
        f"Rain      {forecast.precipitation}% chance",
        f"Humidity  {forecast.humidity}%",
        f"Wind      {forecast.wind}",
    )
    return Response(summary, detail)


def days_response(forecast: Forecast) -> Response:
    """The days ahead in two parts: the first days spoken, a line a day shown."""
    days = forecast.days
    if not days:
        return Response(
            "I have no forecast for the days ahead.",
            (f"Forecast for {forecast.place}", "(no days were returned)"),
        )
    spoken = "; ".join(
        f"{day.name}, {day.condition.lower()} with a high of {day.high}"
        for day in days[:3]
    )
    summary = f"Here is the forecast. {spoken}."
    width = max(len(day.name) for day in days)
    detail = (
        f"Forecast for {forecast.place}",
        *(
            f"{day.name:<{width}}  {day.high:>3}°/{day.low:>3}°  "
            f"{day.precipitation:>3}% rain  {day.condition}"
            for day in days
        ),
    )
    return Response(summary, detail)


class Weather:
    """Look the weather up once and answer, or end with one line of trouble."""

    def __init__(self, launch: Launch) -> None:
        self._definition = launch.definition
        self._days = launch.mode == MODE_FORECAST
        self.stage: Stage = "key"
        self._task: FunctionTask | None = None
        self._key: bytearray | None = None
        self._confirmation: Confirmation | None = None
        self._reply: Reply | None = None
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        definition = self._definition
        if not definition.zip_code:
            self._trouble(face, NO_ZIP_CODE)
            return
        connect = face.seams.connect
        if connect is None or face.seams.weather is None:
            self._trouble(face, NO_CLOUD)
            return
        line = GETTING_DAYS if self._days else GETTING
        face.summary.add(line.format(zip_code=definition.zip_code))
        face.react("working")
        config, repo, secret = face.config, face.repo, definition.secret

        def read_key() -> bytearray:
            return connect(config, repo).secret_value(secret)

        self._task = face.spawn(read_key)
        self.tick(face)

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        if self.stage == "key":
            self._have_key(face)
        elif self.stage == "confirming":
            self._confirming(face)
        elif self.stage == "lookup":
            self._looked_up(face)
        elif self.stage == "answer":
            self._answering(face)

    def cancel(self) -> None:
        """The owner said `cancel`: the key is cleared and nothing more is done."""
        self._drop_key()
        self._done = True

    # --- the steps ----------------------------------------------------------------

    def _have_key(self, face: FaceContext) -> None:
        task = self._task
        assert task is not None
        result = task.poll()
        if result is None:
            return
        self._task = None
        if not result.ok or not isinstance(task.value, bytearray):
            self._trouble(face, result.summary())
            return
        self._key = task.value
        if not self._key.strip():
            self._trouble(face, EMPTY_SECRET.format(secret=self._definition.secret))
            return
        if self._definition.confirm_request:
            self.stage = "confirming"
            self._confirmation = face.confirm(self._definition.confirm_request_question)
            self._confirming(face)
            return
        self._look_up(face)

    def _confirming(self, face: FaceContext) -> None:
        confirmation = self._confirmation
        assert confirmation is not None
        confirmation.tick(face)
        if not confirmation.done:
            return
        self._confirmation = None
        if confirmation.outcome != "yes":
            self._drop_key()
            face.summary.add(NOT_CONFIRMED)
            face.react("done")
            self._done = True
            return
        self._look_up(face)

    def _look_up(self, face: FaceContext) -> None:
        definition = self._definition
        rate = RateControl(
            definition.daily_limit,
            definition.minute_limit,
            face.repo / CALLS_FILE,
            lambda: face.seams.wall_clock().timestamp(),
        )
        lookup = face.seams.weather
        key = self._key
        assert lookup is not None and key is not None
        zip_code, days = definition.zip_code, self._days
        # Each call to the API is counted as it goes out, whatever comes
        # back. Today's weather is two calls (the conditions now, and the
        # day's high, low and chance); the days ahead are one.
        for _ in range(DAYS_CALLS if days else TODAY_CALLS):
            refusal = rate.refusal()
            if refusal:
                self._trouble(face, refusal)
                return
            rate.record()

        def look_up() -> Forecast:
            try:
                return (
                    lookup.days(zip_code, key) if days else lookup.today(zip_code, key)
                )
            finally:
                _clear(key)

        self.stage = "lookup"
        face.react("working")
        self._task = face.spawn(look_up)
        self._looked_up(face)

    def _looked_up(self, face: FaceContext) -> None:
        task = self._task
        assert task is not None
        result = task.poll()
        if result is None:
            return
        self._task = None
        self._drop_key()
        if not result.ok or not isinstance(task.value, Forecast):
            self._trouble(face, result.summary())
            return
        forecast = task.value
        response = days_response(forecast) if self._days else today_response(forecast)
        self.stage = "answer"
        self._reply = Reply(face, response)
        self._answering(face)

    def _answering(self, face: FaceContext) -> None:
        assert self._reply is not None
        self._reply.tick(face)
        if self._reply.done:
            self.stage = "done"
            face.react("done")
            self._done = True

    def _drop_key(self) -> None:
        if self._key is not None:
            _clear(self._key)
            self._key = None

    def _trouble(self, face: FaceContext, reason: str) -> None:
        self._drop_key()
        self.stage = "done"
        face.summary.trouble(reason)
        face.react("trouble")
        self._done = True
