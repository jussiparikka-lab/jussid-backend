import os
import time
import logging
from threading import Thread
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ID4_DAEMON")

app = Flask(__name__)
CORS(app)

VW_USERNAME = os.environ.get("VW_USERNAME", "jussi.parikka@gmail.com")
VW_PASSWORD = os.environ.get("VW_PASSWORD", "R0manialainen2025")

auth_state = {
    "status": "STARTING",
    "message": "Käynnistetään taustapalvelua..."
}

car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "latitude": 60.2941,
    "longitude": 25.0409,
    "locationName": "Odottaa dataa...",
    "lastUpdated": "Ei päivitetty"
}

spot_data = {"hours": [], "cheapHours": []}
weconnect_instance = None

def daemon_loop():
    global auth_state, car_state, weconnect_instance
    
    while True:
        # 1. Pörssisähkö (toimii aina riippumatta VW-yhteydestä)
        try:
            url = "https://api.spot-hinta.fi/TodayAndDayForward"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                prices = [round(item["PriceWithTax"] * 100, 2) for item in data[:24]]
                spot_data["hours"] = prices
                spot_data["cheapHours"] = sorted(range(len(prices)), key=lambda k: prices[k])[:2]
        except Exception as e:
            logger.error(f"❌ [SPOT ERROR]: {e}")

        # 2. VW Connect (Panssaroitu haku)
        try:
            if weconnect_instance is None:
                from weconnect import weconnect as wc
                weconnect_instance = wc.WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    tokenfile=".vw_tokens.json",
                    updateAfterLogin=False
                )

            logger.info("🔑 [VW] Yritetään kirjautumista Volkswagen-palvelimelle...")
            
            # TÄMÄ on se rivi, joka heittää 'location' -bugin kirjaston sisältä
            weconnect_instance.login()
            
            from weconnect.domain import Domain
            weconnect_instance.update(selective=[Domain.CHARGING, Domain.PARKING])
            
            # Tietojen luku turvallisesti
            if hasattr(weconnect_instance, 'vehicles'):
                for vin, vehicle in weconnect_instance.vehicles.items():
                    domains = getattr(vehicle, 'domains', {})
                    if 'charging' in domains:
                        bat = domains['charging'].get('batteryStatus')
                        if bat and getattr(bat, 'currentSOC_pct', None) and bat.currentSOC_pct.value is not None:
                            car_state["currentSoc"] = int(bat.currentSOC_pct.value)
                        if bat and getattr(bat, 'cruisingRangeElectric_km', None) and bat.cruisingRangeElectric_km.value is not None:
                            car_state["rangeKm"] = int(bat.cruisingRangeElectric_km.value)
                    
                    if 'parking' in domains:
                        park = domains['parking'].get('parkingPosition')
                        if park and getattr(park, 'latitude', None) and park.latitude.value is not None:
                            car_state["latitude"] = float(park.latitude.value)
                            car_state["longitude"] = float(park.longitude.value)
                            car_state["locationName"] = f"GPS: {car_state['latitude']:.3f}, {car_state['longitude']:.3f}"

            auth_state["status"] = "CONNECTED"
            auth_state["message"] = "VW Yhdistetty!"
            car_state["lastUpdated"] = time.strftime("%H:%M:%S")
            logger.info(f"✅ [VW] Data päivitetty. Akku: {car_state['currentSoc']}%")

        except KeyError as ke:
            if 'location' in str(ke).lower():
                msg = "VW vaatii 2FA-koodin tai käyttöehtojen hyväksynnän. Kirjasto blokattu."
                logger.error(f"🛑 [VW BLOCKED]: {msg}")
                auth_state["status"] = "2FA_BLOCKED"
                auth_state["message"] = "Kytke 2FA hetkeksi pois VW-tililtäsi tai hyväksy käyttöehdot sovelluksessa."
            else:
                logger.error(f"❌ [VW KeyError]: {ke}")
        except Exception as e:
            logger.error(f"❌ [VW ERROR]: {e}")
            auth_state["status"] = "ERROR"
            auth_state["message"] = str(e)

        time.sleep(120)

@app.route('/')
def index():
    return jsonify({
        "status": "online",
        "auth": auth_state,
        "car": car_state,
        "spot": spot_data
    })

if __name__ == '__main__':
    t = Thread(target=daemon_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
