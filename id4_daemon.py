import os
import time
import json
import logging
from threading import Thread
from flask import Flask, jsonify
import requests

# Konfiguroidaan lokitus
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ID4_DAEMON")

app = Flask(__name__)

# Ympäristömuuttujat Renderistä
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
    "locationName": "Päivitetään...",
    "lastUpdated": "Ei vielä päivitetty",
    "standby": False  # Poistetaan manuaalisäätömoodi
}

spot_data = {"hours": [], "cheapHours": [], "chargeNow": False}

def connect_and_update_vw():
    global car_state
    
    # 1. Jos VW_TOKEN on syötetty Renderiin
    if VW_TOKEN:
        logger.info("🔑 [VW CONNECT] Käytetään tallennetua VW_TOKEN-istuntoa...")
        try:
            from carconnectivity.carconnectivity import CarConnectivity
            
            # Luodaan CarConnectivity-konfiguraatio tokenille
            config = {
                "carConnectivity": {
                    "connectors": [
                        {
                            "type": "volkswagen",
                            "config": {
                                "username": VW_USERNAME,
                                "password": VW_PASSWORD,
                                "token": VW_TOKEN
                            }
                        }
                    ]
                }
            }
            
            cc = CarConnectivity(config=config)
            cc.startup()
            
            # Haetaan autot
            if cc.garage and len(cc.garage.drives) > 0:
                drive = list(cc.garage.drives.values())[0]
                
                # Luetaan akun varaus (SoC) ja toimintasäde
                if hasattr(drive, 'battery') and drive.battery is not None:
                    if hasattr(drive.battery, 'soc') and drive.battery.soc.value is not None:
                        car_state["currentSoc"] = int(drive.battery.soc.value)
                    if hasattr(drive.battery, 'range') and drive.battery.range.value is not None:
                        car_state["rangeKm"] = int(drive.battery.range.value)
                
                # Luetaan GPS-sijainti
                if hasattr(drive, 'position') and drive.position is not None:
                    if hasattr(drive.position, 'latitude') and drive.position.latitude.value is not None:
                        car_state["latitude"] = float(drive.position.latitude.value)
                    if hasattr(drive.position, 'longitude') and drive.position.longitude.value is not None:
                        car_state["longitude"] = float(drive.position.longitude.value)
                        car_state["locationName"] = "ID.4 Sijainti (Autolta)"

                car_state["standby"] = False
                logger.info(f"✅ [VW CONNECT] Auton tiedot haettu! Akku: {car_state['currentSoc']}% | Lat: {car_state['latitude']}")
                return True

        except Exception as e:
            logger.warning(f"⚠️ [VW CONNECT] Virhe rajapintahaussa: {e}")

    # Fallback / Varatila jos yhteys ei muodostu
    logger.info("ℹ️ [VW CONNECT] Odotetaan rajapintadataa...")
    car_state["standby"] = False
    return False

def fetch_spot_prices():
    global spot_data
    try:
        url = "https://api.spot-hinta.fi/TodayAndDayForward"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            prices = [round(item["PriceWithTax"] * 100, 2) for item in data[:24]]
            
            # Etsitään 2 halvinta tuntia
            sorted_hours = sorted(range(len(prices)), key=lambda k: prices[k])[:2]
            
            spot_data["hours"] = prices
            spot_data["cheapHours"] = sorted_hours
            logger.info(f"📈 [SPOT] Tunnit haettu onnistuneesti. Halvimmat tunnit: {sorted_hours}")
    except Exception as e:
        logger.error(f"❌ [SPOT ERROR] Virhe pörssisähköhaussa: {e}")

def daemon_loop():
    while True:
        logger.info("[DAEMON] Päivitetään ajoneuvon ja pörssisähkön tiedot...")
        connect_and_update_vw()
        fetch_spot_prices()
        car_state["lastUpdated"] = time.strftime("%H:%M:%S")
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
