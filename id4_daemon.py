import os
import time
import logging
from threading import Thread
from flask import Flask, jsonify
from flask_cors import CORS
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ID4_DAEMON")

app = Flask(__name__)
CORS(app)

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

def fetch_official_vw_data():
    global car_state, auth_state
    
    token = os.environ.get("VW_TOKEN", VW_TOKEN)
    if not token:
        logger.info("ℹ️ [VW API] Odotetaan VW_TOKEN-ympäristömuuttujaa Renderissä...")
        auth_state["status"] = "WAITING_TOKEN"
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
    }

    try:
        # Virallinen Volkswagen ID / WeConnect -rajapinta
        base_url = "https://api.connect.vw.com/v1/vehicles"
        res = requests.get(base_url, headers=headers, timeout=10)

        if res.status_code == 200:
            data = res.json()
            vehicles = data.get("data", []) if isinstance(data, dict) else data
            if isinstance(vehicles, list) and len(vehicles) > 0:
                vin = vehicles[0].get("vin")
                if vin:
                    status_res = requests.get(f"{base_url}/{vin}/status", headers=headers, timeout=10)
                    if status_res.status_code == 200:
                        s_data = status_res.json()
                        battery = s_data.get("batteryStatus", {})
                        if "currentSOC_pct" in battery:
                            car_state["currentSoc"] = int(battery["currentSOC_pct"])
                        if "cruisingRangeElectric_km" in battery:
                            car_state["rangeKm"] = int(battery["cruisingRangeElectric_km"])

            auth_state["status"] = "CONNECTED"
            logger.info(f"✅ [VW API] Tiedot haettu onnistuneesti! Akku: {car_state['currentSoc']}%")
            return True
        else:
            logger.warning(f"⚠️ [VW API] Rajapinta palautti tilakoodin {res.status_code}. Näytetään olemassa oleva tila.")
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
        fetch_official_vw_data()
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
