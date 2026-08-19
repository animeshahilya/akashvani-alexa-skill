import difflib
import json
import logging
import random
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
from ask_sdk_model.ui import SimpleCard

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

# Not dead - these play fine in the Tarang app and pass this skill's own probe - but fail
# specifically on a real Echo device. CloudWatch-verified 2026-08-19: both this station's primary
# and its own independently-hosted backup mirror (added the same day, see akashvani/scraper's
# dedupe_stations()) returned AudioPlayer.PlaybackFailed with MEDIA_ERROR_INVALID_REQUEST - an
# HTTP-level rejection from the server, not a network/TLS failure (the TLS certificate chain was
# checked and validates cleanly). Both URLs are hosted by the same CDN operator
# (mystreaming.net/uber.radio), which most likely blocks Amazon's own request path specifically -
# Radio Browser's full public database has no third, independently-hosted mirror for this station
# to fall back to. Kept separate from EXCLUDED_STATIONS above since the reason and the right user-
# facing message are both different (a genuinely dead stream vs. one that's alive everywhere else).
ALEXA_INCOMPATIBLE_STATIONS = frozenset([
    "Mirchi Love",
])

# The single set every matching/discovery function actually filters against - a station excluded
# for either reason is equally "don't offer this," they just needed separate documentation above.
_UNPLAYABLE_STATIONS = EXCLUDED_STATIONS | ALEXA_INCOMPATIBLE_STATIONS

def find_alexa_incompatible_match(requested_name):
    """Returns the ALEXA_INCOMPATIBLE_STATIONS name [requested_name] refers to, or None - requires
    the same case-insensitive *exact* match find_station() itself needs before assuming a specific
    station was meant, so a vague/fuzzy request doesn't get this specific message by accident."""
    if not requested_name:
        return None
    requested = requested_name.lower().strip()
    for name in ALEXA_INCOMPATIBLE_STATIONS:
        if name.lower() == requested:
            return name
    return None

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
    available = [s for s in stations if s.get("name") not in _UNPLAYABLE_STATIONS]

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

# Marks a stream token as already-a-backup-retry, so PlaybackFailed only ever falls back once per
# playback attempt instead of looping if the backup is also broken. Deliberately not "|" or other
# punctuation that could plausibly appear in a scraped station name.
_BACKUP_TOKEN_SUFFIX = "::backup"

def find_backup_url(stations, station_name):
    """Returns the backup_url for a station name, or None if it doesn't have one. Most of the
    catalog doesn't (~126 of ~3100 do, per a live check of akashvani-data)."""
    for s in stations:
        if s.get("name") == station_name and s.get("backup_url"):
            return s["backup_url"]
    return None

def find_stations_by_language(stations, requested_language):
    """Returns every non-excluded station whose `language` field carries [requested_language] as
    one of its whole comma/slash-separated entries (the catalog stores multi-language stations as
    "Rajasthani, Hindi" or "Tamil/English") - a plain substring check would also match "english"
    against "American English", which isn't what someone asking for English radio means."""
    if not requested_language:
        return []
    requested = requested_language.lower().strip()
    if not requested:
        return []
    matches = []
    for s in stations:
        if s.get("name") in _UNPLAYABLE_STATIONS:
            continue
        tokens = {t.strip().lower() for t in re.split(r"[,/]", s.get("language", "")) if t.strip()}
        if requested in tokens:
            matches.append(s)
    return matches

def pick_random_station(stations):
    """Picks a random non-dead station for users who don't have a specific name in mind yet -
    the exact same knows-nothing-about-radio-station-names person the "discover a station" intent
    exists for shouldn't get a dead catalog entry."""
    available = [s for s in stations if s.get("name") not in _UNPLAYABLE_STATIONS]
    if not available:
        return None
    return random.choice(available)

def _play_response(handler_input, station, speech, card_title="Now Playing"):
    stream_url = station["stream_url"]
    station_name = station["name"]

    session_attr = handler_input.attributes_manager.session_attributes
    session_attr["last_station_name"] = station_name
    session_attr["last_stream_url"] = stream_url
    handler_input.attributes_manager.session_attributes = session_attr

    return (
        handler_input.response_builder
            .speak(speech)
            .set_card(SimpleCard(card_title, station_name))
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

class LaunchRequestHandler(AbstractRequestHandler):
    # A handful of well-known, verified-alive stations (not a random pull from the full ~3300-
    # entry catalog) to name in the very first thing the skill ever says to someone - a brand-new
    # user has no station names to reach for, and naming one specific option ("want to hear X?")
    # is a much smaller ask than an open-ended "which station would you like?" with nothing to
    # anchor on. Kept as a short curated list rather than pick_random_station()'s full pool so a
    # first impression is never a station nobody outside its home city would recognize.
    FEATURED_STATIONS = ["Vividh Bharati", "Mirchi FM", "Fever FM", "Radio City FM 98.8"]

    # Varied so a skill someone opens daily doesn't greet them with the exact same line every
    # time - a standard Alexa VUI convention for anything a user hits repeatedly.
    GREETING_TEMPLATES = [
        "Welcome to Tarang! Want to hear {name}, or tell me another station?",
        "Hey there! How about {name} to start, or name your own station?",
        "Tarang here! I can queue up {name} for you, or you can name any station you like.",
    ]

    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        featured_name = random.choice(self.FEATURED_STATIONS)

        session_attr = handler_input.attributes_manager.session_attributes
        session_attr["suggested_station_name"] = featured_name
        handler_input.attributes_manager.session_attributes = session_attr

        speech_text = random.choice(self.GREETING_TEMPLATES).format(name=featured_name)
        reprompt = f"Say yes for {featured_name}, name another station, or say discover a station."
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask(reprompt)
                .set_card(SimpleCard(
                    "Tarang",
                    f"Want to hear {featured_name}? Say yes, name another station, or say \"discover a station.\""
                ))
                .response
        )

class PlayStationIntentHandler(AbstractRequestHandler):
    # Kept short (unlike the greeting/farewell variety) since this line is spoken right before
    # audio starts and needs to get out of the way quickly - but even three options stop it from
    # sounding like a fixed script on every single request.
    PLAYING_PHRASES = ["Playing {name}.", "Here's {name}.", "Tuning in to {name} now."]

    def can_handle(self, handler_input):
        return is_intent_name("PlayStationIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        station_slot = slots.get("station_name")

        if not station_slot or not station_slot.value:
            speech = "Sure! Which station would you like me to play?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        stations = fetch_stations()
        matched_station, options = find_station(stations, station_slot.value)

        if not matched_station:
            incompatible_name = find_alexa_incompatible_match(station_slot.value)
            if incompatible_name:
                speech = (
                    f"{incompatible_name} isn't available through Alexa right now - its "
                    "streaming provider doesn't work with Echo devices, though it still plays "
                    "fine in the Tarang app. Which other station would you like?"
                )
                return handler_input.response_builder.speak(speech).ask(
                    "Which other station would you like?"
                ).response

            if options:
                names = [o["name"] for o in options]
                if len(names) == 1:
                    speech = f"I couldn't find an exact match for {station_slot.value}, but did you mean {names[0]}? Just say the name to play it."
                else:
                    listed = ", ".join(names[:-1]) + f", or {names[-1]}"
                    speech = f"I couldn't find an exact match for {station_slot.value}. Did you mean {listed}? Say one of those to play it."
                return handler_input.response_builder.speak(speech).ask(speech).response

            speech = f"Hmm, I couldn't find a station called {station_slot.value}. Want to try another name, or say discover a station?"
            return handler_input.response_builder.speak(speech).ask("Which station would you like to try, or say discover a station?").response

        speech = random.choice(self.PLAYING_PHRASES).format(name=matched_station["name"])
        return _play_response(handler_input, matched_station, speech)

class DiscoverStationIntentHandler(AbstractRequestHandler):
    """Handles "discover a station" / "surprise me" / "I don't know" - the easiest possible way
    for someone with zero familiarity with the catalog to get audio playing on their very first
    try, instead of the skill's only path forward being "you must already know an exact station
    name.\""""
    DISCOVER_PHRASES = [
        "Discovered {name} for you.",
        "Here's something new: {name}.",
        "Let's give {name} a listen.",
    ]

    def can_handle(self, handler_input):
        return is_intent_name("DiscoverStationIntent")(handler_input)

    def handle(self, handler_input):
        station = pick_random_station(fetch_stations())
        if not station:
            speech = "Sorry, I couldn't find any stations to play right now. Please try again shortly."
            return handler_input.response_builder.speak(speech).response

        speech = random.choice(self.DISCOVER_PHRASES).format(name=station["name"])
        return _play_response(handler_input, station, speech, card_title="Discover Pick")

class PlayLanguageIntentHandler(AbstractRequestHandler):
    """Handles "play hindi radio" / "play tamil music" / "punjabi station" - a listener who wants
    *a* station in a language, not one specific name, previously had no path to that short of
    already knowing a station name in that language. RADIO_LANGUAGE's values in the interaction
    model are the catalog's own most common single-language tokens (see generate_model.py)."""
    PLAYING_PHRASES = [
        "Here's a {language} station: {name}.",
        "Playing {name}.",
        "Tuning in to {name}, a {language} station.",
    ]

    def can_handle(self, handler_input):
        return is_intent_name("PlayLanguageIntent")(handler_input)

    def handle(self, handler_input):
        slots = handler_input.request_envelope.request.intent.slots
        language_slot = slots.get("language")

        if not language_slot or not language_slot.value:
            speech = "Sure! Which language would you like?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        matches = find_stations_by_language(fetch_stations(), language_slot.value)
        if not matches:
            speech = (
                f"Sorry, I couldn't find any {language_slot.value} stations right now. "
                "Which station would you like, or say discover a station?"
            )
            return handler_input.response_builder.speak(speech).ask(
                "Which station would you like, or say discover a station?"
            ).response

        station = random.choice(matches)
        speech = random.choice(self.PLAYING_PHRASES).format(name=station["name"], language=language_slot.value)
        return _play_response(handler_input, station, speech)

class YesIntentHandler(AbstractRequestHandler):
    """Only meaningful right after LaunchRequestHandler names a specific featured station and
    asks "want to hear X?" - session_attr["suggested_station_name"] is how that suggestion
    survives to this turn. A bare "yes" with nothing suggested (e.g. mid-conversation, out of
    context) has nothing to confirm, so it's treated like a fresh "which station" prompt."""
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.YesIntent")(handler_input)

    def handle(self, handler_input):
        session_attr = handler_input.attributes_manager.session_attributes
        suggested_name = session_attr.get("suggested_station_name")

        if not suggested_name:
            speech = "Sorry, I'm not sure what you're saying yes to. Which station would you like?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        matched_station, _ = find_station(fetch_stations(), suggested_name)
        if not matched_station:
            speech = f"Sorry, {suggested_name} isn't available right now. Which station would you like instead?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        speech = f"Great choice! Playing {matched_station['name']}."
        return _play_response(handler_input, matched_station, speech)

class NoIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.NoIntent")(handler_input)

    def handle(self, handler_input):
        speech = "No problem! Which station would you like, or say discover a station and I'll pick one for you."
        return handler_input.response_builder.speak(speech).ask(speech).response

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
            speech = "I don't have anything paused to resume. Which station would you like?"
            return handler_input.response_builder.speak(speech).ask(speech).response

        speech = f"Welcome back! Resuming {station_name}."
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
        speech = (
            "I'm Tarang, and I can play thousands of live radio stations. "
            "Just say a station name, like 'play Radio Mirchi', and I'll start playing it. "
            "Not sure what to pick? Say 'discover a station' and I'll choose one for you. "
            "You can also say pause, resume, or stop anytime. Which station would you like?"
        )
        return (
            handler_input.response_builder
                .speak(speech)
                .ask("Which station would you like to listen to?")
                .response
        )

class CancelOrStopIntentHandler(AbstractRequestHandler):
    FAREWELLS = ["Goodbye! Thanks for tuning in.", "See you next time!", "Catch you later!"]

    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        speech = random.choice(self.FAREWELLS)
        return (handler_input.response_builder
                .speak(speech)
                .add_directive(StopDirective())
                .response)

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        speech = "Sorry, I didn't catch that. Which station would you like to hear?"
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
    AudioPlayer.* event silently, including failures.

    PlaybackFailed additionally retries once via the station's backup_url when one exists (~126
    of ~3100 stations have one). AudioPlayer requests can arrive with no active voice session -
    handler_input.attributes_manager.session_attributes raises in that case - so retry state is
    tracked in the stream token itself (a "::backup" suffix) rather than session attributes.

    Once there's no backup left to try, the response speaks a "can't play this right now" message
    and reprompts instead of returning empty - see the handle() method's own comment for why."""
    def can_handle(self, handler_input):
        # NOT request.type - the ask-sdk-model deserializer maps the JSON "type" field to a
        # Python attribute called object_type (attribute_map = {'object_type': 'type', ...}),
        # so .type raises AttributeError on every real AudioPlayer request. That exception would
        # get caught by CatchAllExceptionHandler and spoken aloud, interrupting playback with
        # "Sorry, there was a problem..." on every single AudioPlayer event a real device sends -
        # this was silent in testing because the browser's text simulator never truly decodes
        # audio, so it never emits genuine AudioPlayer.* requests to catch this against.
        return handler_input.request_envelope.request.object_type.startswith("AudioPlayer.")

    def handle(self, handler_input):
        request = handler_input.request_envelope.request
        if request.object_type == "AudioPlayer.PlaybackFailed":
            token = getattr(getattr(request, "current_playback_state", None), "token", None) or ""
            logger.error(f"AudioPlayer.PlaybackFailed for token={token!r}: {getattr(request, 'error', None)}")

            is_backup_attempt = token.endswith(_BACKUP_TOKEN_SUFFIX)
            if not is_backup_attempt:
                backup_url = find_backup_url(fetch_stations(), token)
                if backup_url:
                    logger.info(f"Retrying {token!r} via its backup_url after PlaybackFailed")
                    return (
                        handler_input.response_builder
                            .add_directive(
                                PlayDirective(
                                    play_behavior=PlayBehavior.REPLACE_ALL,
                                    audio_item={
                                        "stream": {
                                            "token": token + _BACKUP_TOKEN_SUFFIX,
                                            "url": backup_url,
                                            "offset_in_milliseconds": 0
                                        }
                                    }
                                )
                            )
                            .response
                    )

            # Genuinely out of options for this station - no backup_url exists, or the backup
            # itself just failed too. Previously this fell straight through to the plain empty
            # response below, which Alexa has nothing to say for: dead air, with no way to recover
            # short of the user starting a whole new request from scratch. This is the general
            # safety net for any station that fails unpredictably, not just ones already known and
            # hand-excluded (see ALEXA_INCOMPATIBLE_STATIONS, which skips the failed attempt
            # entirely for stations already confirmed broken - this is what catches everything
            # else).
            station_name = token[:-len(_BACKUP_TOKEN_SUFFIX)] if is_backup_attempt else token
            if station_name:
                speech = (
                    f"Sorry, I can't play {station_name} right now. Would you like to try a "
                    "different station?"
                )
                return handler_input.response_builder.speak(speech).ask(
                    "Which station would you like to try?"
                ).response
        return handler_input.response_builder.response

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)
        speech = "Oops, something went wrong on my end. Please try again in a moment."
        return handler_input.response_builder.speak(speech).response

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PlayStationIntentHandler())
sb.add_request_handler(DiscoverStationIntentHandler())
sb.add_request_handler(PlayLanguageIntentHandler())
sb.add_request_handler(YesIntentHandler())
sb.add_request_handler(NoIntentHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(AudioPlayerEventHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
