import json
import os
import urllib.request

STATIONS_JSON_URL = "https://raw.githubusercontent.com/animeshahilya/akashvani-data/main/stations.json"

def generate_model():
    print(f"Fetching stations from {STATIONS_JSON_URL}")
    # stdlib instead of requests - requirements.txt no longer installs requests/urllib3 at all
    # (removed to fix the Lambda's OpenSSL crash), so this script would otherwise fail with
    # ModuleNotFoundError on the very next CI run.
    req = urllib.request.Request(STATIONS_JSON_URL, headers={"User-Agent": "akashvani-alexa-skill"})
    with urllib.request.urlopen(req, timeout=15) as response:
        stations = json.loads(response.read().decode("utf-8"))
    
    # Stations confirmed dead by directly probing their stream URLs (real HTTP status + content-
    # type, not just guessing from the file extension) - see check_streams.py. Kept out of the
    # slot type too, not just the Lambda's runtime matching, so they're never offered as a
    # recognized station name in the first place.
    excluded_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excluded_stations.json")
    with open(excluded_path, encoding="utf-8") as f:
        excluded_names = set(json.load(f))

    # Extract unique station names for the custom slot type
    # Alexa rejects any slot value over 140 characters - a handful of scraped entries
    # are garbled multi-station blobs well past that, so they're dropped rather than
    # truncated (a truncated blob isn't a name anyone would actually say either).
    MAX_SLOT_VALUE_LENGTH = 140
    station_names = set()
    for s in stations:
        name = s.get("name")
        if name and name not in excluded_names:
            # Basic cleanup for Alexa slot values (alphanumeric and spaces)
            cleaned = "".join(c for c in name if c.isalnum() or c.isspace()).strip()
            if cleaned and len(cleaned) <= MAX_SLOT_VALUE_LENGTH:
                station_names.add(cleaned)
    
    slot_values = [{"name": {"value": name}} for name in sorted(list(station_names))]

    # The catalog's own most common single-language tokens (language fields like "Rajasthani,
    # Hindi" are comma/slash-separated - see lambda_function.py's find_stations_by_language()),
    # each with a real, non-trivial number of stations behind it as of a live catalog check
    # (2026-08-19) - a hand-picked list rather than deriving one from every distinct token found,
    # since the raw data also has one-off noise (stray zero-width characters, "Ml", zero-count
    # combinations) that isn't something anyone would actually ask Alexa for by name.
    RADIO_LANGUAGES = [
        "Hindi", "Tamil", "English", "Urdu", "Punjabi", "Malayalam", "Bengali", "Nepali",
        "Telugu", "Marathi", "Kannada", "Gujarati", "Odia", "Bhojpuri", "Rajasthani",
        "Assamese", "Konkani", "Haryanvi",
    ]
    language_values = [{"name": {"value": language}} for language in RADIO_LANGUAGES]

    interaction_model = {
        "interactionModel": {
            "languageModel": {
                "invocationName": "tarang",
                "intents": [
                    {
                        "name": "AMAZON.CancelIntent",
                        "samples": []
                    },
                    {
                        "name": "AMAZON.HelpIntent",
                        "samples": []
                    },
                    {
                        "name": "AMAZON.StopIntent",
                        "samples": []
                    },
                    {
                        "name": "AMAZON.PauseIntent",
                        "samples": []
                    },
                    {
                        "name": "AMAZON.ResumeIntent",
                        "samples": []
                    },
                    {
                        "name": "PlayStationIntent",
                        "slots": [
                            {
                                "name": "station_name",
                                "type": "RADIO_STATION"
                            }
                        ],
                        "samples": [
                            "play {station_name}",
                            "start {station_name}",
                            "tune in to {station_name}",
                            "listen to {station_name}",
                            "put on {station_name}",
                            "play station {station_name}",
                            "{station_name}"
                        ]
                    },
                    {
                        # No slot - lets a user with no station name in mind get audio playing on
                        # the very first try instead of needing to already know the catalog.
                        # "Discover" is the skill's own name for this ("discover a station" is
                        # what the skill itself says); the other samples are just how people
                        # actually phrase the same request in the wild.
                        "name": "DiscoverStationIntent",
                        "samples": [
                            "discover a station",
                            "discover something new",
                            "discover a new station",
                            "help me discover a station",
                            "discover",
                            "surprise me",
                            "play something",
                            "play anything",
                            "play something random",
                            "play a random station",
                            "you choose",
                            "you pick",
                            "you pick one",
                            "i don't know",
                            "i don't know you pick",
                            "play whatever"
                        ]
                    },
                    {
                        # A listener who wants *a* station in a language, not one specific name -
                        # "play hindi radio" previously had no route at all (PlayStationIntent
                        # would look for a station literally named "hindi radio" and fail).
                        "name": "PlayLanguageIntent",
                        "slots": [
                            {
                                "name": "language",
                                "type": "RADIO_LANGUAGE"
                            }
                        ],
                        "samples": [
                            "play {language} radio",
                            "play {language} stations",
                            "play {language} music",
                            "play some {language} radio",
                            "play a {language} station",
                            "i want to listen to {language} radio",
                            "listen to {language} radio",
                            "{language} radio",
                            "{language} music",
                            "{language} station"
                        ]
                    },
                    {
                        "name": "AMAZON.YesIntent",
                        "samples": []
                    },
                    {
                        "name": "AMAZON.NoIntent",
                        "samples": []
                    },
                    {
                        # Built-in intents only get routed to by Alexa's NLU if they're declared
                        # here - lambda_function.py already registers a FallbackIntentHandler, but
                        # without this entry the model has nowhere to send an utterance that misses
                        # every PlayStationIntent sample template, so Alexa's own generic system
                        # fallback speaks instead of the skill's own handler. This was silently
                        # missing, making the skill look like it randomly "didn't hear" a station
                        # request whenever the phrasing didn't fit "play/start/tune in to/listen
                        # to/put on {station_name}" exactly.
                        "name": "AMAZON.FallbackIntent",
                        "samples": []
                    }
                ],
                "types": [
                    {
                        "name": "RADIO_STATION",
                        "values": slot_values
                    },
                    {
                        "name": "RADIO_LANGUAGE",
                        "values": language_values
                    }
                ]
            }
        }
    }
    
    os.makedirs("models", exist_ok=True)
    with open("models/en-IN.json", "w") as f:
        json.dump(interaction_model, f, indent=2)
        
    print(f"Successfully generated interaction model for {len(slot_values)} stations at models/en-IN.json")

if __name__ == "__main__":
    generate_model()
