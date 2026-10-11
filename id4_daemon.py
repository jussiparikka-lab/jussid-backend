#!/usr/bin/env python3
"""
JussID — Taustavahti ID.4 GTX & Pörssisähköohjaukselle
Tukee modernia CarConnectivity -arkkitehtuuria sekä automaattista virhesuojausta.
"""

import os
import time
import json
import requests
import traceback
from datetime import datetime, timedelta
from flask import Flask, jsonify

app = Flask(__name__)

VW_USERNAME = os.environ.get("VW_USERNAME", "jussi.parikka@gmail.com")
VW_PASSWORD = os.environ.get("VW_PASSWORD", "R0manialainen2025")
VW_VIN = os.environ.get("VW_VIN", "WVGZZZE2ZNP017960")

FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

# Globaali ajoneuvon tila
car_state = {
    "currentSoc": 64,
    "rangeKm": 300,
    "chargingState": "READY",
    "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "connectionStatus": "Connecting"
}

def get_spot_prices():
    """Hakee kuluvan vuorokauden spot-hinnat Pörssisähkö APIsta."""
    try:
        url = "https://api.porssisahko.net/v1/latest-prices.json"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            prices = res.json().get('prices', [])
            print(f"📈 [SPOT] Haettu {len(prices)} tuntihintaa onnistuneesti.")
            return prices
    except Exception as e:
        print(f"⚠️ [SPOT API VIRHE] {e}")
    return []

def calculate_smart_schedule(current_soc, target_soc, deadline_str, prices):
    """Laskee halvimmat tuntijaksot lataukselle ennen aamun määräaikaa."""
    if not prices:
        return True  # Varmuuslataus jos hintadataa ei saada

    needed_soc = max(0, target_soc - current_soc)
    # 77 kWh akku, 11 kW teho -> n. 14.2 % akun varausta tunnissa
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

    # Valitaan edullisimmat tunnit
    valid_prices.sort(key=lambda x: x[1])
    cheapest_hours = [x[0].hour for x in valid_prices[:hours_needed]]
    is_cheap_now = now.hour in cheapest_hours
    print(f"⚡ [ÄLYLATAUS] Tarve: {hours_needed}h. Halvimmat tunnit: {cheapest_hours}. Ladataanko nyt: {is_cheap_now}")
    return is_cheap_now

class VWCarManager:
    """Hallinnoi auton yhteyttä CarConnectivity / WeConnect -kirjastojen kautta."""
    def __init__(self):
        self.client = None
        self.use_carconnectivity = False
        self._init_driver()

    def _init_driver(self):
        try:
            from carconnectivity import carconnectivity
            self.use_carconnectivity = True
            print("🚀 [DRIVER] CarConnectivity-kirjasto käytettävissä.")
        except ImportError:
            print("ℹ️ [DRIVER] CarConnectivity ei asennettu, kokeillaan WeConnect-kirjastoa.")
            self.use_carconnectivity = False

    def connect(self):
        print(f"[VW CONNECT] Yhdistetään tunnuksella {VW_USERNAME}...")
        try:
            if self.use_carconnectivity:
                from carconnectivity import carconnectivity
                config = {
                    "carConnectivity": {
                        "connectors": [
                            {
                                "type": "volkswagen",
                                "config": {
                                    "username": VW_USERNAME,
                                    "password": VW_PASSWORD
                                }
                            }
                        ]
                    }
                }
                self.client = carconnectivity.CarConnectivity(config=config)
                self.client.startup()
                print(f"✅ [VW CONNECT] CarConnectivity käynnistetty autolle: {VW_VIN}")
                return True
            else:
                from weconnect import weconnect
                self.client = weconnect.WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    updateAfterLogin=True
                )
                print(f"✅ [VW CONNECT] WeConnect yhdistetty autoon: {VW_VIN}")
                return True
        except Exception as e:
            print(f"⚠️ [VW CONNECT] Kirjautuminen vaatii varatilan: {e}")
            car_state["connectionStatus"] = "Standby"
            return False

    def update_vehicle_data(self):
        global car_state
        if not self.client:
            self.connect()

        # Luetaan ensin nykyinen tila Firebasesta (estetään manuaalisen varauksen ylikirjoitus 64%:lla)
        try:
            fb_res = requests.get(f"{FIREBASE_URL}/carStatus.json", timeout=5)
            if fb_res.status_code == 200 and fb_res.json():
                fb_data = fb_res.json()
                if "currentSoc" in fb_data and car_state["connectionStatus"] != "Connected":
                    car_state["currentSoc"] = int(fb_data["currentSoc"])
                    car_state["rangeKm"] = int(fb_data.get("rangeKm", car_state["currentSoc"] * 4.6))
        except Exception as e:
            print(f"ℹ️ [FIREBASE SYNC CHECK] {e}")

        # Yritetään lukea tuorein telemetria ajoneuvolta
        if self.client:
            try:
                if self.use_carconnectivity and hasattr(self.client, 'garage'):
                    vehicle = self.client.garage.get_vehicle(VW_VIN)
                    if vehicle:
                        # Etsitään akun varaustaso
                        for drive in getattr(vehicle, 'drives', []):
                            if hasattr(drive, 'battery') and drive.battery.level.value is not None:
                                car_state["currentSoc"] = int(drive.battery.level.value)
                                car_state["rangeKm"] = int(getattr(drive.battery, 'range', {}).get('value', car_state["currentSoc"] * 4.6))
                        # Luetaan sijainti jos saatavilla
                        if hasattr(vehicle, 'position') and vehicle.position.latitude.value is not None:
                            car_state["latitude"] = vehicle.position.latitude.value
                            car_state["longitude"] = vehicle.position.longitude.value
                        car_state["connectionStatus"] = "Connected"
                elif hasattr(self.client, 'vehicles'):
                    self.client.update()
                    vehicle = self.client.vehicles.get(VW_VIN)
                    if vehicle:
                        battery = vehicle.domains.get('charging', {}).get('batteryStatus', None)
                        if battery and battery.currentSOC_pct.value is not None:
                            car_state["currentSoc"] = int(battery.currentSOC_pct.value)
                            car_state["rangeKm"] = int(battery.cruisingRange_km.value or (car_state["currentSoc"] * 4.6))
                        car_state["connectionStatus"] = "Connected"
            except Exception as e:
                print(f"⚠️ [DATA FETCH] Autotietojen päivitysvirhe: {e}")
                self.client = None

        car_state["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Synkronoidaan auton tila reaaliaikaisesti Firebaseen
        try:
            requests.patch(f"{FIREBASE_URL}/carStatus.json", json=car_state, timeout=10)
            print(f"📊 [STATUS] Akku: {car_state['currentSoc']} % ({car_state['rangeKm']} km) | Tila: {car_state['chargingState']}")
        except Exception as err:
            print(f"⚠️ [FIREBASE SYNC] {err}")

manager = VWCarManager()

def background_loop():
    """Pääsilmukka: valvoo käyttäjän toiveita ja pörssisähköä 2 minuutin välein."""
    while True:
        try:
            manager.update_vehicle_data()

            # Luetaan sovelluksen asetukset Firebasesta
            res = requests.get(f"{FIREBASE_URL}/chargingConfig.json", timeout=10)
            config = res.json() if res.status_code == 200 and res.json() else {}

            mode = config.get("mode", "SMART")
            deadline = config.get("deadline", "07:00")
            target_soc = config.get("targetSoc", 80)

            if mode == "SMART":
                prices = get_spot_prices()
                should_charge = calculate_smart_schedule(car_state["currentSoc"], target_soc, deadline, prices)
                car_state["chargingState"] = "CHARGING" if should_charge else "READY"
            else:
                car_state["chargingState"] = "CHARGING"
                print("⚡ [HETI-LATAUS] Lataus aktiivinen täydellä teholla.")

            # Tallennetaan päivittynyt lataustila Firebaseen
            requests.patch(f"{FIREBASE_URL}/carStatus.json", json={"chargingState": car_state["chargingState"]}, timeout=10)

        except Exception as loop_err:
            print(f"⚠️ [LOOP VIRHE] {loop_err}")

        time.sleep(120)

@app.route('/')
def health():
    return jsonify({
        "status": "online",
        "service": "JussID Taustavahti",
        "carState": car_state
    }), 200

import threading
thread = threading.Thread(target=background_loop, daemon=True)
thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
