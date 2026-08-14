import difflib
import json
import logging
import re
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

# Confirmed dead by directly probing every station's stream URL (checked actual HTTP status and
# content-type, not just file extension) - see check_streams.py / excluded_stations.json in the
# repo for the audit that produced this list. This is a static snapshot, not live-revalidated on
# every request (probing ~3100 URLs per invocation would blow well past Lambda's time budget) -
# it needs re-running periodically as akashvani-data changes, but keeps the catalog from offering
# stations we already know can't play.
EXCLUDED_STATIONS = frozenset([
    "101.9WFAN",
    "105.3 the fan Dallas sports talk radio",
    "AIR Port Blair PC",
    "Akashvani Churachandpur",
    "Akashvani Hospet",
    "Akashvani Pauri",
    "Akashvani Ziro",
    "Bardiya Online Radio",
    "ESPN 1100AM / 100.9FM",
    "Eagle Country 99.3 FM WSCH",
    "Gurudwara Dashmesh Culture",
    "Janadhwani Kannada",
    "La Mera Mera 980",
    "Lakecity Voice",
    "RADIO JASGOLD",
    "Raaj FM",
    "Radio Awaz",
    "Radio Dil",
    "Radio Gurbaba",
    "Radio Hot FM 105 Naushero Feroz",
    "Radio Janapriya",
    "Radio Morning Star",
    "Radio Naya Karnali",
    "Radio Noida 107.4 FM",
    "Radio Paigam-e-Shabad Guru",
    "Radio Prakriti",
    "Radio Prakriti - 93.4 MHz FM, Tulsīpur, Nepal",
    "Radio Taplejung",
    "Radio The Wolf",
    "SLBC Asia Service",
    "SikhNet Radio - Gurdwara Bangla Sahib - Delhi - India",
    "Sikhnet Radio - Dashmesh Culture Center",
    "Sikhnet Radio - Fremont",
    "Traffic Scotland Radio",
    "Vagad Radio 90.8",
    "lmrradio",
    "sajeevavahini",
])

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}

def _normalize_spoken_numbers(text):
    """Alexa's ASR transcribes digits as words ("24" -> "twenty four"), but station names in the
    catalog keep the literal digits, so "twenty four fm" would never match "24 FM" without this."""
    words = text.split()
    out = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _NUMBER_WORDS and _NUMBER_WORDS[w] >= 20 and i + 1 < len(words) and words[i + 1] in _NUMBER_WORDS and _NUMBER_WORDS[words[i + 1]] < 10:
            out.append(str(_NUMBER_WORDS[w] + _NUMBER_WORDS[words[i + 1]]))
            i += 2
            continue
        out.append(str(_NUMBER_WORDS[w]) if w in _NUMBER_WORDS else w)
        i += 1
    return " ".join(out)

def find_station(stations, requested_name, max_options=3):
    """Returns (matched_station, options).

    matched_station is set only when there's a single, confident match (exact name, or every word
    of the request present as a whole word in exactly one station's name). Anything less certain -
    multiple whole-word matches, or only a fuzzy near-match - comes back as a short options list
    instead of silently guessing which one the user meant.
    """
    if not requested_name:
        return None, []
    requested = requested_name.lower().strip()
    if not requested:
        return None, []

    candidates = {requested, _normalize_spoken_numbers(requested)}
    available = [s for s in stations if s.get("name") not in EXCLUDED_STATIONS]

    for s in available:
        if s.get("name", "").lower() in candidates:
            return s, []

    whole_word_matches = []
    matched_names = set()
    for cand in candidates:
        req_words = set(re.findall(r"\w+", cand))
        if not req_words:
            continue
        for s in available:
            name = s.get("name", "")
            if name in matched_names:
                continue
            name_words = set(re.findall(r"\w+", name.lower()))
            if req_words <= name_words:
                whole_word_matches.append(s)
                matched_names.add(name)

    if len(whole_word_matches) == 1:
        return whole_word_matches[0], []
    if len(whole_word_matches) > 1:
        return None, whole_word_matches[:max_options]

    by_lower_name = {s.get("name", "").lower(): s for s in available}
    fuzzy_names = []
    for cand in candidates:
        for match in difflib.get_close_matches(cand, list(by_lower_name.keys()), n=max_options, cutoff=0.72):
            if match not in fuzzy_names:
                fuzzy_names.append(match)

    if fuzzy_names:
        return None, [by_lower_name[n] for n in fuzzy_names[:max_options]]

    return None, []

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
        matched_station, options = find_station(stations, station_slot.value)

        if not matched_station:
            if options:
                names = [o["name"] for o in options]
                if len(names) == 1:
                    speech = f"I couldn't find an exact match for {station_slot.value}. Did you mean {names[0]}? Just say the station name to play it."
                else:
                    listed = ", ".join(names[:-1]) + f", or {names[-1]}"
                    speech = f"I couldn't find an exact match for {station_slot.value}. Did you mean {listed}? Say one of those to play it."
                return handler_input.response_builder.speak(speech).ask(speech).response

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
