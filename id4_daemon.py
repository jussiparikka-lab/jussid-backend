import os
import time
import logging
from threading import Thread
from flask import Flask, jsonify
from flask_cors import CORS
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ID4_DAEMON")

# =====================================================================
# 🚨 MONKEYPATCH: OHITETAAN VOLKSWAGENIN BOTTISUOJA (CLOUDFLARE/AKAMAI) 🚨
# Pakotetaan kaikki WeConnectin taustapyynnöt näyttämään aidolta iPhonelta,
# jotta Renderin pilvi-IP ei jää kiinni VW:n palomuuriin.
# =====================================================================
original_session_init = requests.Session.__init__

def spoofed_session_init(self, *args, **kwargs):
    original_session_init(self, *args, **kwargs)
    self.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    })

requests.Session.__init__ = spoofed_session_init
# =====================================================================

app = Flask(__name__)
CORS(app)

VW_USERNAME = os.environ.get("VW_USERNAME", "")
VW_PASSWORD = os.environ.get("VW_PASSWORD", "")

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
        # Pörssisähkö
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

        # Volkswagen
        try:
            if not VW_USERNAME or not VW_PASSWORD:
                raise ValueError("VW_USERNAME tai VW_PASSWORD puuttuu Renderin asetuksista!")

            if weconnect_instance is None:
                from weconnect import weconnect as wc
                weconnect_instance = wc.WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    tokenfile=".vw_tokens.json",
                    updateAfterLogin=False
                )

            logger.info("🔑 [VW] Kirjaudutaan sisään naamioidulla selaimella...")
            weconnect_instance.login()
            
            from weconnect.domain import Domain
            weconnect_instance.update(selective=[Domain.CHARGING, Domain.PARKING])
            
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
            logger.info(f"✅ [VW] Data päivitetty! Akku: {car_state['currentSoc']}%")

        except KeyError as ke:
            if 'location' in str(ke).lower():
                logger.error("🛑 [VW BLOCKED]: Renderin IP on edelleen estetty VW:n palomuurissa. Naamiointi ei riittänyt.")
                auth_state["status"] = "IP_BLOCKED"
                auth_state["message"] = "VW estää pilvipalvelimen (Render)."
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
