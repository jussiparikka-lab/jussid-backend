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
    "status": "WAITING_2FA",
    "message": "Anna sähköpostiisi tullut 6-numeroinen VW-koodi sovellukseen.",
    "code_submitted": None
}

car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "chargingState": "READY",
    "plugState": "DISCONNECTED",
    "latitude": 60.2941,
    "longitude": 25.0409,
    "locationName": "ID.4 Pysäköity",
    "lastUpdated": "Ei vielä päivitetty",
    "standby": False
}

spot_data = {"hours": [], "cheapHours": [], "chargeNow": False}

def init_weconnect():
    global car_state, auth_state
    try:
        from weconnect import weconnect as wc
        
        logger.info("🚀 [VW CONNECT] Aloitetaan WeConnect-autentikointi...")
        
        weconnect_instance = wc.WeConnect(
            username=VW_USERNAME,
            password=VW_PASSWORD,
            tokenfile=".vw_tokens.json"
        )

        if not os.path.exists(".vw_tokens.json"):
            auth_state["status"] = "WAITING_2FA"
            logger.info("🔑 [VW CONNECT] Odotetaan 2FA-koodia...")
            
            timeout = 300
            start = time.time()
            while auth_state["code_submitted"] is None:
                if time.time() - start > timeout:
                    auth_state["status"] = "ERROR"
                    auth_state["message"] = "2FA-koodin syöttö aikakatkaistiin."
                    return False
                time.sleep(2)

            code = auth_state["code_submitted"]
            logger.info(f"📩 [VW CONNECT] Lähetetään koodi: {code}")

        weconnect_instance.login()
        weconnect_instance.update()

        # Luetaan ajoneuvon tiedot täysin suojatusti
        for vin, vehicle in getattr(weconnect_instance, 'vehicles', {}).items():
            domains = getattr(vehicle, 'domains', {})
            
            # 1. Akku ja toimintasäde
            if 'charging' in domains:
                charging = domains['charging']
                bat_status = getattr(charging, 'batteryStatus', None) if not isinstance(charging, dict) else charging.get('batteryStatus')
                if bat_status:
                    soc_attr = getattr(bat_status, 'currentSOC_pct', None)
                    if soc_attr and hasattr(soc_attr, 'value') and soc_attr.value is not None:
                        car_state["currentSoc"] = int(soc_attr.value)
                        
                    range_attr = getattr(bat_status, 'cruisingRangeElectric_km', None)
                    if range_attr and hasattr(range_attr, 'value') and range_attr.value is not None:
                        car_state["rangeKm"] = int(range_attr.value)

            # 2. GPS-sijainti
            if 'parking' in domains:
                parking = domains['parking']
                park_pos = getattr(parking, 'parkingPosition', None) if not isinstance(parking, dict) else parking.get('parkingPosition')
                if park_pos:
                    lat_attr = getattr(park_pos, 'latitude', None)
                    lon_attr = getattr(park_pos, 'longitude', None)
                    if lat_attr and lon_attr and hasattr(lat_attr, 'value') and hasattr(lon_attr, 'value'):
                        if lat_attr.value is not None and lon_attr.value is not None:
                            car_state["latitude"] = float(lat_attr.value)
                            car_state["longitude"] = float(lon_attr.value)
                            car_state["locationName"] = f"ID.4 ({car_state['latitude']:.4f}, {car_state['longitude']:.4f})"

        auth_state["status"] = "CONNECTED"
        auth_state["message"] = "VW Connect Yhdistetty!"
        car_state["standby"] = False
        logger.info(f"✅ [VW CONNECT] Tiedot haettu! Akku: {car_state['currentSoc']}% | Lat: {car_state['latitude']}")
        return True

    except Exception as e:
        logger.error(f"❌ [VW CONNECT Virhe]: {e}")
        auth_state["status"] = "ERROR"
        auth_state["message"] = str(e)
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
        if auth_state["status"] != "CONNECTED":
            init_weconnect()
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

@app.route('/submit-2fa', methods=['POST'])
def submit_2fa():
    data = request.get_json()
    code = data.get("code") if data else None
    if code:
        auth_state["code_submitted"] = str(code).strip()
        return jsonify({"status": "ok", "message": "Koodi vastaanotettu, käsitellään..."})
    return jsonify({"status": "error", "message": "Virheellinen koodi"}), 400

if __name__ == '__main__':
    t = Thread(target=daemon_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
