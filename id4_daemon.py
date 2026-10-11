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
VW_TOKEN = os.environ.get("VW_TOKEN", None)

auth_state = {
    "status": "WAITING_TOKEN" if not VW_TOKEN else "CONNECTED",
    "message": "Aktiivinen"
}

car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "chargingState": "READY",
    "plugState": "DISCONNECTED",
    "latitude": 60.2941,
    "longitude": 25.0409,
    "locationName": "Vantaa (Pysäköity)",
    "lastUpdated": "Ei vielä päivitetty",
    "standby": False
}

spot_data = {"hours": [], "cheapHours": [], "chargeNow": False}

def fetch_vw_direct_telemetry():
    global car_state, auth_state
    
    token = os.environ.get("VW_TOKEN", VW_TOKEN)
    if not token:
        logger.info("ℹ️ [VW API] Odotetaan VW_TOKEN-ympäristömuuttujaa Renderissä...")
        auth_state["status"] = "WAITING_TOKEN"
        return False

    logger.info("🔑 [VW API] Haetaan tiedot suoraan VW Rajapinnasta...")
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    try:
        # Haetaan auton tilatiedot (SoC, Range)
        url = "https://wecharge-prod.apps.emea.vwapps.io/v1/vehicles"
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            # Jos data saatiin, päivitetään car_state
            if isinstance(data, list) and len(data) > 0:
                v = data[0]
                car_state["currentSoc"] = v.get("battery", {}).get("currentSOC_pct", car_state["currentSoc"])
                car_state["rangeKm"] = v.get("battery", {}).get("cruisingRangeElectric_km", car_state["rangeKm"])
            
            auth_state["status"] = "CONNECTED"
            logger.info(f"✅ [VW API] Tiedot päivitetty! Akku: {car_state['currentSoc']}%")
            return True
        else:
            logger.warning(f"⚠️ [VW API] Rajapinta palautti koodin {res.status_code}. Käytetään tallennettua tilaa.")
            auth_state["status"] = "CONNECTED"
            return True

    except Exception as e:
        logger.error(f"❌ [VW API Virhe]: {e}")
        auth_state["status"] = "CONNECTED"
        return False

def fetch_spot_prices():
    global spot_data
    try:
        url = "https://api.spot-hinta.fi/TodayAndDayForward"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            prices = [round(item["PriceWithTax"] * 100, 2) for item in data[:24]]
            sorted_hours = sorted(range(len(prices)), key=lambda k: prices[k])[:2]
            spot_data["hours"] = prices
            spot_data["cheapHours"] = sorted_hours
    except Exception as e:
        logger.error(f"❌ [SPOT ERROR]: {e}")

def daemon_loop():
    while True:
        fetch_vw_direct_telemetry()
        fetch_spot_prices()
        car_state["lastUpdated"] = time.strftime("%H:%M:%S")
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
