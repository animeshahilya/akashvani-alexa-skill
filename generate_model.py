import json
import os
import requests

STATIONS_JSON_URL = "https://raw.githubusercontent.com/animeshahilya/akashvani-data/main/stations.json"

def generate_model():
    print(f"Fetching stations from {STATIONS_JSON_URL}")
    response = requests.get(STATIONS_JSON_URL)
    response.raise_for_status()
    stations = response.json()
    
    # Extract unique station names for the custom slot type
    station_names = set()
    for s in stations:
        name = s.get("name")
        if name:
            # Basic cleanup for Alexa slot values (alphanumeric and spaces)
            cleaned = "".join(c for c in name if c.isalnum() or c.isspace()).strip()
            if cleaned:
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
                                "type": "RADIO_STATION",
                                "samples": [
                                    "{station_name}"
                                ]
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
