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
    "message": "Odotetaan 2FA-koodia...",
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
        from weconnect.domain import Domain
        
        logger.info("🚀 [VW CONNECT] Aloitetaan WeConnect-kirjautuminen...")
        
        # Alustetaan WeConnect ilman pakollista location-domainia
        weconnect_instance = wc.WeConnect(
            username=VW_USERNAME,
            password=VW_PASSWORD,
            tokenfile=".vw_tokens.json"
        )

        # Pyydetään vain latausdomain, jottei location-virhe kaada prosessia
        weconnect_instance.selective = [Domain.CHARGING, Domain.PARKING]

        if not os.path.exists(".vw_tokens.json"):
            auth_state["status"] = "WAITING_2FA"
            logger.info("🔑 [VW CONNECT] Odotetaan koodia käyttöliittymästä...")
            
            timeout = 300
            start = time.time()
            while auth_state["code_submitted"] is None:
                if time.time() - start > timeout:
                    auth_state["status"] = "ERROR"
                    auth_state["message"] = "2FA-koodin syöttö aikakatkaistiin."
                    return False
                time.sleep(2)

            code = auth_state["code_submitted"]
            logger.info(f"📩 [VW CONNECT] Lähetetään 2FA: {code}")

        # Kirjaudutaan sisään
        weconnect_instance.login()
        weconnect_instance.update()

        # Haetaan lataustiedot turvallisesti
        if hasattr(weconnect_instance, 'vehicles'):
            for vin, vehicle in weconnect_instance.vehicles.items():
                if hasattr(vehicle, 'domains'):
                    # Akku
                    if 'charging' in vehicle.domains:
                        charging = vehicle.domains['charging']
                        if 'batteryStatus' in charging:
                            car_state["currentSoc"] = int(charging['batteryStatus'].currentSOC_pct.value)
                            car_state["rangeKm"] = int(charging['batteryStatus'].cruisingRangeElectric_km.value)
                    
                    # Sijainti jos saatavilla
                    if 'parking' in vehicle.domains:
                        parking = vehicle.domains['parking']
                        if 'parkingPosition' in parking and parking['parkingPosition'].latitude.value:
                            car_state["latitude"] = float(parking['parkingPosition'].latitude.value)
                            car_state["longitude"] = float(parking['parkingPosition'].longitude.value)

        auth_state["status"] = "CONNECTED"
        auth_state["message"] = "VW Connect Yhdistetty!"
        car_state["standby"] = False
        logger.info(f"✅ [VW CONNECT] Tiedot haettu! Akku: {car_state['currentSoc']}%")
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
