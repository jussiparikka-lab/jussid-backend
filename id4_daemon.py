#!/usr/bin/env python3
import os
import time
import json
import requests
import traceback
from datetime import datetime, timedelta
from flask import Flask, jsonify
from weconnect import weconnect

app = Flask(__name__)

VW_USERNAME = os.environ.get("VW_USERNAME", "jussi.parikka@gmail.com")
VW_PASSWORD = os.environ.get("VW_PASSWORD", "R0manialainen2025")
VW_VIN = os.environ.get("VW_VIN", "WVGZZZE2ZNP017960")
VW_TOKEN_STR = os.environ.get("VW_TOKEN", "").strip()

FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

# Globaali tila auton arvoille
car_state = {
    "currentSoc": 0,
    "rangeKm": 0,
    "chargingState": "UNKNOWN",
    "lastUpdated": ""
}

def get_spot_prices():
    """Hakee kuluvan vuorokauden spot-hinnat Pörssisähkö APIsta."""
    try:
        url = "https://api.porssisahko.net/v1/latest-prices.json"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json().get('prices', [])
    except Exception as e:
        print(f"⚠️ [SPOT API VIRHE] {e}")
    return []

def calculate_smart_schedule(current_soc, target_soc, deadline_str, prices):
    """Laskee halvimmat tuntijaksot lataukselle ennen määräaikaa."""
    if not prices:
        return True  # Jos hintoja ei saada, ladataan varmuuden vuoksi

    needed_soc = max(0, target_soc - current_soc)
    # 77 kWh akku, 11 kW teho -> n. 14.2 % akun varausta per tunti
    hours_needed = int(-(-needed_soc // 14.2)) if needed_soc > 0 else 0

    if hours_needed == 0:
        return False

    now = datetime.now()
    try:
        deadline_time = datetime.strptime(deadline_str, "%H:%M").time()
        deadline_dt = datetime.combine(now.date(), deadline_time)
        if deadline_dt <= now:
            deadline_dt = datetime.combine(now.date() + timedelta(days=1), deadline_time)
    except Exception:
        deadline_dt = datetime.combine(now.date(), datetime.strptime("07:00", "%H:%M").time())

    # Suodatetaan spot-tunnit nykyhetken ja deadlinen väliltä
    valid_prices = []
    for p in prices:
        try:
            price_dt = datetime.fromisoformat(p['startDate'].replace('Z', '+00:00')).replace(tzinfo=None)
            if now <= price_dt <= deadline_dt:
                valid_prices.append((price_dt, p['price']))
        except Exception:
            continue

    if not valid_prices:
        return True

    # Järjestetään hinnan mukaan ja valitaan halvimmat tarvittavat tunnit
    valid_prices.sort(key=lambda x: x[1])
    cheapest_hours = [x[0].hour for x in valid_prices[:hours_needed]]

    return now.hour in cheapest_hours

class ID4Daemon:
    def __init__(self):
        self.weconnect = None
        self.token_file_path = "/tmp/weconnect.token"

    def setup_token_file(self):
        """Kirjoittaa tokenin tiedostoon jos annettu ympäristömuuttujana."""
        if VW_TOKEN_STR:
            try:
                with open(self.token_file_path, "w", encoding="utf-8") as f:
                    f.write(VW_TOKEN_STR)
                print("🔑 [VW CONNECT] VW_TOKEN ladattu ympäristömuuttujasta tiedostoon.")
                return True
            except Exception as e:
                print(f"⚠️ [TOKEN VIRHE] Ei voitu kirjoittaa token-tiedostoa: {e}")
        return False

    def connect(self):
        print(f"[VW CONNECT] Kirjaudutaan Volkswagen-palvelimelle ({VW_USERNAME})...")
        self.setup_token_file()

        try:
            # Jos token-tiedosto on olemassa, käytetään sitä suoraan
            if os.path.exists(self.token_file_path):
                self.weconnect = weconnect.WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    tokenfile=self.token_file_path,
                    updateAfterLogin=True
                )
            else:
                self.weconnect = weconnect.WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    updateAfterLogin=True
                )

            print(f"✅ [VW CONNECT] Yhdistetty autoon: {VW_VIN}")
            return True

        except Exception as e:
            print(f"❌ [VW CONNECT ERROR] Kirjautumisvirhe: {e}")
            print("[VW CONNECT DIAGNOSTIIKKA]:")
            traceback.print_exc()

            # Tarkistetaan onko WeConnectin sessiolokiin tallentunut virhevastetta
            try:
                if hasattr(self, 'weconnect') and self.weconnect and hasattr(self.weconnect, 'session'):
                    last_resp = getattr(self.weconnect.session, 'last_response', None)
                    if last_resp:
                        print(f"DEBUG Response status: {last_resp.status_code}")
                        print(f"DEBUG Response preview: {last_resp.text[:300]}")
            except Exception:
                pass

            print("⏳ Odotetaan 60 sekuntia ennen uutta yritystä (estetään IP-lukitus)...")
            time.sleep(60)
            return False

    def update_data(self):
        global car_state
        if not self.weconnect:
            if not self.connect():
                return

        try:
            self.weconnect.update()
            vehicle = self.weconnect.vehicles.get(VW_VIN)
            if not vehicle:
                print(f"⚠️ Autoa VIN-koodilla {VW_VIN} ei löytynyt tililtä.")
                return

            # Akun varaustaso ja toimintasäde
            battery = vehicle.domains.get('charging', {}).get('batteryStatus', None)
            if battery and battery.currentSOC_pct.value is not None:
                car_state["currentSoc"] = battery.currentSOC_pct.value
                if hasattr(battery, 'cruisingRange_km') and battery.cruisingRange_km.value is not None:
                    car_state["rangeKm"] = battery.cruisingRange_km.value
                else:
                    car_state["rangeKm"] = int(car_state["currentSoc"] * 4.6)

            charging = vehicle.domains.get('charging', {}).get('chargingStatus', None)
            if charging and charging.chargingState.value is not None:
                car_state["chargingState"] = str(charging.chargingState.value)

            car_state["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"📊 [STATUS] Akku: {car_state['currentSoc']} % ({car_state['rangeKm']} km) | Tila: {car_state['chargingState']}")

            # Päivitetään tiedot Firebaseen
            requests.patch(f"{FIREBASE_URL}/carStatus.json", json=car_state, timeout=10)

        except Exception as e:
            print(f"⚠️ [PÄIVITYSVIRHE] {e}")
            self.weconnect = None  # Nollataan sessio uutta kirjautumista varten

    def run_control_loop(self):
        while True:
            try:
                self.update_data()

                # Luetaan käyttäjän asetukset Firebasesta
                res = requests.get(f"{FIREBASE_URL}/chargingConfig.json", timeout=10)
                config = res.json() if res.status_code == 200 and res.json() else {}

                mode = config.get("mode", "IMMEDIATE")
                deadline = config.get("deadline", "07:00")
                target_soc = config.get("targetSoc", 80)

                if mode == "SMART":
                    prices = get_spot_prices()
                    should_charge = calculate_smart_schedule(car_state["currentSoc"], target_soc, deadline, prices)
                    print(f"⚡ [ÄLYLATAUS] Pitääkö ladata nyt: {'KYLLÄ' if should_charge else 'EI (odotetaan halpaa tuntia)'}")
                else:
                    print("⚡ [HETI-LATAUS] Lataus sallittu täydellä teholla.")

            except Exception as e:
                print(f"⚠️ [SILMUKKAVIRHE] {e}")

            time.sleep(120)  # Tarkistus 2 minuutin välein

daemon = ID4Daemon()

@app.route('/')
def home():
    return jsonify({"status": "running", "carState": car_state})

def start_daemon():
    import threading
    t = threading.Thread(target=daemon.run_control_loop, daemon=True)
    t.start()

start_daemon()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
