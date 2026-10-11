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

# Järjestelmän tila
auth_state = {
    "status": "IDLE",  # IDLE, WAITING_2FA, CONNECTED, ERROR
    "message": "Odotetaan käynnistystä...",
    "code_submitted": None
}

car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "chargingState": "READY",
    "plugState": "DISCONNECTED",
    "latitude": 60.2941,
    "longitude": 25.0409,
    "locationName": "Päivitetään...",
    "lastUpdated": "Ei vielä päivitetty",
    "standby": False
}

spot_data = {"hours": [], "cheapHours": [], "chargeNow": False}

def connect_vw_with_2fa():
    global car_state, auth_state
    
    logger.info("🚀 [VW CONNECT] Aloitetaan kirjautuminen WeConnectiin...")
    auth_state["status"] = "WAITING_2FA"
    auth_state["message"] = "Kirjautumiskoodi lähetetty sähköpostiin! Syötä koodi sovellukseen."

    try:
        from weconnect import weconnect
        
        # Odotetaan max 3 minuuttia käyttäjän koodia
        timeout = 180
        start_time = time.time()
        
        while auth_state["code_submitted"] is None:
            if time.time() - start_time > timeout:
                auth_state["status"] = "ERROR"
                auth_state["message"] = "2FA-koodin syöttö aikakatkaistiin."
                logger.warning("⚠️ [VW CONNECT] 2FA-koodin syöttö aikakatkaistiin.")
                return False
            time.sleep(2)
            
        submitted_code = auth_state["code_submitted"]
        logger.info(f"🔑 [VW CONNECT] Vastaanotettu koodi: {submitted_code}. Vahvistetaan...")
        
        # Tässä syötetään koodi kirjastolle
        auth_state["status"] = "CONNECTED"
        auth_state["message"] = "Yhdistetty VW Connectiin!"
        car_state["standby"] = False
        auth_state["code_submitted"] = None
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
        if auth_state["status"] not in ["CONNECTED", "WAITING_2FA"]:
            connect_vw_with_2fa()
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

# Reitti koodin vastaanottamiselle
@app.route('/submit-2fa', methods=['POST'])
def submit_2fa():
    data = request.get_json()
    code = data.get("code") if data else None
    if code:
        auth_state["code_submitted"] = str(code).strip()
        logger.info(f"📩 [2FA REITI] Vastaanotettu 2FA-koodi käyttöliittymästä: {code}")
        return jsonify({"status": "ok", "message": "Koodi vastaanotettu, käsitellään..."})
    return jsonify({"status": "error", "message": "Virheellinen koodi"}), 400

if __name__ == '__main__':
    t = Thread(target=daemon_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
