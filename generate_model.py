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
                    }
                ],
                "types": [
                    {
                        "name": "RADIO_STATION",
                        "values": slot_values
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
