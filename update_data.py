import os
import json
import time
import logging
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VW_UPDATE")

VW_USERNAME = os.environ.get("VW_USERNAME")
VW_PASSWORD = os.environ.get("VW_PASSWORD")

# Monkeypatch palomuurien varalta
original_session_init = requests.Session.__init__
def spoofed_session_init(self, *args, **kwargs):
    original_session_init(self, *args, **kwargs)
    self.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
    })
requests.Session.__init__ = spoofed_session_init

def get_spot_data():
    try:
        url = "https://api.spot-hinta.fi/TodayAndDayForward"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            prices = [round(item["PriceWithTax"] * 100, 2) for item in res.json()[:24]]
            return {"hours": prices, "cheapHours": sorted(range(len(prices)), key=lambda k: prices[k])[:2]}
    except Exception as e:
        logger.error(f"Spot-virhe: {e}")
    return {"hours": [], "cheapHours": []}

def get_vw_data():
    car_state = {
        "currentSoc": None, "rangeKm": None, "latitude": None, "longitude": None,
        "locationName": "Ei dataa", "lastUpdated": time.strftime("%H:%M:%S")
    }
    if not VW_USERNAME or not VW_PASSWORD:
        logger.error("Tunnukset puuttuvat.")
        return car_state, "Virhe: Tunnukset puuttuvat"

    try:
        from weconnect import weconnect as wc
        from weconnect.domain import Domain
        weconnect_instance = wc.WeConnect(
            username=VW_USERNAME, password=VW_PASSWORD,
            tokenfile=".vw_tokens.json", updateAfterLogin=False
        )
        weconnect_instance.login()
        weconnect_instance.update(selective=[Domain.CHARGING, Domain.PARKING])
        
        if hasattr(weconnect_instance, 'vehicles'):
            for vin, vehicle in weconnect_instance.vehicles.items():
                domains = getattr(vehicle, 'domains', {})
                if 'charging' in domains:
                    bat = domains['charging'].get('batteryStatus')
                    if bat and getattr(bat, 'currentSOC_pct', None):
                        car_state["currentSoc"] = int(bat.currentSOC_pct.value)
                    if bat and getattr(bat, 'cruisingRangeElectric_km', None):
                        car_state["rangeKm"] = int(bat.cruisingRangeElectric_km.value)
                if 'parking' in domains:
                    park = domains['parking'].get('parkingPosition')
                    if park and getattr(park, 'latitude', None):
                        car_state["latitude"] = float(park.latitude.value)
                        car_state["longitude"] = float(park.longitude.value)
                        car_state["locationName"] = f"GPS: {car_state['latitude']:.3f}, {car_state['longitude']:.3f}"
        return car_state, "Yhdistetty"
    except Exception as e:
        logger.error(f"VW-virhe: {e}")
        return car_state, str(e)

def main():
    spot_data = get_spot_data()
    car_data, auth_msg = get_vw_data()
    
    output = {
        "status": "online",
        "auth": {"status": "CONNECTED" if car_data["currentSoc"] else "ERROR", "message": auth_msg},
        "car": car_data,
        "spot": spot_data
    }
    
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("data.json päivitetty.")

if __name__ == "__main__":
    main()
