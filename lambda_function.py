import json
import logging
import time
import urllib.request
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_model.interfaces.audioplayer import (
    PlayDirective, PlayBehavior, StopDirective, ClearQueueDirective, ClearBehavior
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATIONS_JSON_URL = "https://raw.githubusercontent.com/animeshahilya/akashvani-data/main/stations.json"

# Lambda containers are reused across invocations ("warm start"), so this cache saves a ~380KB
# fetch on every single utterance within the same warm container, not just within one session.
_stations_cache = {"data": None, "fetched_at": 0.0}
CACHE_TTL_SECONDS = 600

def fetch_stations():
    now = time.time()
    if _stations_cache["data"] is not None and (now - _stations_cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _stations_cache["data"]

    last_exception = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(STATIONS_JSON_URL, headers={"User-Agent": "akashvani-alexa-skill"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            _stations_cache["data"] = data
            _stations_cache["fetched_at"] = now
            return data
        except Exception as e:
            last_exception = e
            logger.warning(f"fetch_stations attempt {attempt + 1} failed: {e}")

    logger.error(f"Failed to fetch stations after retries: {last_exception}")
    if _stations_cache["data"] is not None:
        # Degrade to a stale list rather than an empty one - a few-minutes-old station list
        # is far more useful to a user than "I can't find any stations at all."
        logger.warning("Serving stale cached station list after fetch failure.")
        return _stations_cache["data"]
    return []

def find_station(stations, requested_name):
    if not requested_name:
        return None
    requested = requested_name.lower().strip()
    if not requested:
        return None

    for s in stations:
        if s.get("name", "").lower() == requested:
            return s

    for s in stations:
        if requested in s.get("name", "").lower():
            return s

    return None

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        speech_text = "Welcome to Tarang. Which station would you like to listen to?"
        return handler_input.response_builder.speak(speech_text).ask(speech_text).response

class PlayStationIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("PlayStationIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        station_slot = slots.get("station_name")

        if not station_slot or not station_slot.value:
            speech = "Please specify a station name."
            return handler_input.response_builder.speak(speech).ask(speech).response

        stations = fetch_stations()
        matched_station = find_station(stations, station_slot.value)

        if not matched_station:
            speech = f"Sorry, I could not find a station named {station_slot.value}."
            return handler_input.response_builder.speak(speech).ask("Try another station.").response

        stream_url = matched_station["stream_url"]
        station_name = matched_station["name"]

        session_attr = handler_input.attributes_manager.session_attributes
        session_attr["last_station_name"] = station_name
        session_attr["last_stream_url"] = stream_url
        handler_input.attributes_manager.session_attributes = session_attr

        speech = f"Playing {station_name}."

        return (
            handler_input.response_builder
                .speak(speech)
                .add_directive(
                    PlayDirective(
                        play_behavior=PlayBehavior.REPLACE_ALL,
                        audio_item={
                            "stream": {
                                "token": station_name,
                                "url": stream_url,
                                "offset_in_milliseconds": 0
                            }
                        }
                    )
                )
                .response
        )

class PauseIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PauseIntent")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.add_directive(StopDirective()).response

class ResumeIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)

    def handle(self, handler_input):
        # Only resumes within the current session (session_attributes aren't persisted across
        # sessions/devices - there's no DynamoDB persistence adapter wired up). Good enough for
        # "pause then resume a minute later"; a fresh session still has nothing to resume.
        session_attr = handler_input.attributes_manager.session_attributes
        station_name = session_attr.get("last_station_name")
        stream_url = session_attr.get("last_stream_url")

        if not station_name or not stream_url:
            speech = "I don't have a station to resume. Which station would you like?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        speech = f"Resuming {station_name}."
        return (
            handler_input.response_builder
                .speak(speech)
                .add_directive(
                    PlayDirective(
                        play_behavior=PlayBehavior.REPLACE_ALL,
                        audio_item={
                            "stream": {
                                "token": station_name,
                                "url": stream_url,
                                "offset_in_milliseconds": 0
                            }
                        }
                    )
                )
                .response
        )

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speech = "You can say play, followed by a station name, to start listening. Which station would you like?"
        return handler_input.response_builder.speak(speech).ask(speech).response

class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        speech = "Goodbye!"
        return (handler_input.response_builder
                .speak(speech)
                .add_directive(StopDirective())
                .response)

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        speech = "I didn't understand that. Which station would you like?"
        return handler_input.response_builder.speak(speech).ask(speech).response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response

class AudioPlayerEventHandler(AbstractRequestHandler):
    """Most AudioPlayer events are informational no-ops, but PlaybackFailed is logged with its
    error details - a real, non-trivial fraction of the station catalog has broken stream URLs
    (see akashvani-data), and this was previously invisible since the handler dropped every
    AudioPlayer.* event silently, including failures."""
    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.type.startswith("AudioPlayer.")

    def handle(self, handler_input):
        request = handler_input.request_envelope.request
        if request.type == "AudioPlayer.PlaybackFailed":
            token = getattr(getattr(request, "current_playback_state", None), "token", None)
            logger.error(f"AudioPlayer.PlaybackFailed for token={token!r}: {getattr(request, 'error', None)}")
        return handler_input.response_builder.response

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)
        speech = "Sorry, there was a problem handling your request."
        return handler_input.response_builder.speak(speech).response

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PlayStationIntentHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(AudioPlayerEventHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
