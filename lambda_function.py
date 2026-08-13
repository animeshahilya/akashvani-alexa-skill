import logging
import requests
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

def fetch_stations():
    try:
        response = requests.get(STATIONS_JSON_URL, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch stations: {e}")
        return []

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
            
        requested_station = station_slot.value.lower()
        stations = fetch_stations()
        
        # Try to find a match
        matched_station = None
        for s in stations:
            if s.get("name", "").lower() == requested_station:
                matched_station = s
                break
        
        if not matched_station:
            # Fallback to partial match
            for s in stations:
                if requested_station in s.get("name", "").lower():
                    matched_station = s
                    break
        
        if not matched_station:
            speech = f"Sorry, I could not find a station named {requested_station}."
            return handler_input.response_builder.speak(speech).ask("Try another station.").response
        
        stream_url = matched_station["stream_url"]
        station_name = matched_station["name"]
        
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
        # A proper implementation would save the current token and resume.
        # For live radio streams, offset doesn't matter much. We just stop for now.
        speech = "To resume, please ask for the station again."
        return handler_input.response_builder.speak(speech).response

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
    """Handle AudioPlayer events silently."""
    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.type.startswith("AudioPlayer.")

    def handle(self, handler_input):
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
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(AudioPlayerEventHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
