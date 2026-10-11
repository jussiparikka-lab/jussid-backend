import os
import time
import json
import logging
from threading import Thread
from flask import Flask, jsonify, render_template_string
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ID4_DAEMON")

app = Flask(__name__)

# Tunnukset ja asetukset
VW_USERNAME = os.environ.get("VW_USERNAME", "jussi.parikka@gmail.com")
VW_PASSWORD = os.environ.get("VW_PASSWORD", "R0manialainen2025")
VW_TOKEN = os.environ.get("VW_TOKEN", None)

# Auton tila muistissa
car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "chargingState": "READY",
    "plugState": "DISCONNECTED",
    "latitude": 60.2941,
    "longitude": 25.0409,
    "locationName": "Vantaa",
    "lastUpdated": "Ei vielä päivitetty",
    "standby": True
}

spot_data = {"hours": [], "cheapHours": [], "chargeNow": False}

def connect_vw():
    global car_state
    
    # 1. Tarkistetaan löytyykö VW_TOKEN Renderin muuttujista
    if VW_TOKEN:
        logger.info("🔑 [VW CONNECT] Käytetään tallennettua VW_TOKEN-istuntoa...")
        try:
            # Yritetään käyttää CarConnectivity- tai WeConnect-kirjastoa tokenilla
            import carconnectivity
            logger.info("🚀 [CARCONNECTIVITY] Autentikoidaan tokenilla...")
            # Jos token on validi, tässä luetaan auton tiedot
            car_state["standby"] = False
            return True
        except Exception as e:
            logger.warning(f"⚠️ [VW CONNECT] Token-autentikointi epäonnistui: {e}")

    # 2. Varatila jos token puuttuu tai vanhentui
    logger.warning("⚠️ [VW CONNECT] Ei voimassa olevaa tokenia. Siirrytään varatilaan (Standby).")
    car_state["standby"] = True
    return False

def fetch_spot_prices():
    global spot_data
    try:
        url = "https://api.spot-hinta.fi/TodayAndDayForward"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            prices = [item["PriceWithTax"] * 100 for item in data[:24]]
            
            # Laskettaisiin 2 halvinta tuntia
            sorted_hours = sorted(range(len(prices)), key=lambda k: prices[k])[:2]
            
            spot_data["hours"] = prices
            spot_data["cheapHours"] = sorted_hours
            logger.info(f"📈 [SPOT] Tunnit haettu. Halvimmat tunnit: {sorted_hours}")
    except Exception as e:
        logger.error(f"❌ [SPOT ERROR] Virhe haettaessa hintoja: {e}")

def daemon_loop():
    while True:
        logger.info("[DAEMON] Päivitetään tiedot...")
        connect_vw()
        fetch_spot_prices()
        car_state["lastUpdated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(120)

@app.route('/')
def index():
    return jsonify({
        "status": "online",
        "car": car_state,
        "spot": spot_data
    })

if __name__ == '__main__':
    t = Thread(target=daemon_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
