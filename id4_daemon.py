import os
import time
import datetime
import threading
import requests
from flask import Flask

try:
    from weconnect import WeConnect
    WE_CONNECT_AVAILABLE = True
except ImportError:
    WE_CONNECT_AVAILABLE = False

# =========================================================================
# FIREBASE & VW CONNECT TUNNUKSET
# =========================================================================
FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

VW_USERNAME = "jussi.parikka@gmail.com"
VW_PASSWORD = "R0manialainen2025"
VW_VIN = "WVGZZZE2ZNP017960"

# =========================================================================
# PIENI VALEPALVELIN (Pitävät Render Web Servicen käynnissä 24/7)
# =========================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "JussID Daemon is running smoothly!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# =========================================================================
# APUFUNKTIOT: PÖRSSISÄHKÖ & FIREBASE
# =========================================================================
def get_spot_prices():
    """ Hakee Suomen spot-hinnat kuluvalle ja seuraavalle vuorokaudelle """
    try:
        url = "https://spot-hinta.fi/api/v1/todayAndTomorrow"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return sorted(data, key=lambda x: x['DateTime'])
    except Exception as e:
        print(f"[PÖRSSISÄHKÖ ERROR] Hinnanhaku epäonnistui: {e}")
    return []

def update_firebase(path, data):
    """ Päivittää tietoja Firebase-tietokantaan """
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        requests.patch(url, json=data, timeout=10)
    except Exception as e:
        print(f"[FIREBASE ERROR] Päivitys epäonnistui: {e}")

def get_firebase_state():
    """ Lukee nykyiset asetukset Firebasesta """
    try:
        url = f"{FIREBASE_URL}/chargingConfig.json"
        res = requests.get(url, timeout=10)
        if res.status_code == 200 and res.json():
            return res.json()
    except Exception as e:
        print(f"[FIREBASE ERROR] Tilan luku epäonnistui: {e}")
    return {"mode": "IMMEDIATE", "deadline": "07:00", "targetSoc": 80}

# =========================================================================
# VOLKSWAGEN VW-CONNECT INTEGRAATIO
# =========================================================================
class ID4Controller:
    def __init__(self):
        self.weconnect = None
        self.vehicle = None
        self.connect()

    def connect(self):
        """ Yhdistää Volkswagen-rajapintaan """
        if not WE_CONNECT_AVAILABLE:
            print("[VW CONNECT] TÄRKEÄÄ: 'weconnect'-kirjastoa ei ole asennettu! Ajetaan simulaatiotilassa.")
            return

        try:
            print("[VW CONNECT] Kirjaudutaan Volkswagen-palvelimelle...")
            self.weconnect = WeConnect(
                username=VW_USERNAME,
                password=VW_PASSWORD,
                updateAfterLogin=True,
                loginAndInitialize=True
            )
            if VW_VIN in self.weconnect.vehicles:
                self.vehicle = self.weconnect.vehicles[VW_VIN]
                print(f"[VW CONNECT] Yhdistetty autoon: {self.vehicle.nickname or VW_VIN}")
            else:
                print(f"[VW CONNECT ERROR] Autoa VIN-koodilla {VW_VIN} ei löytynyt tililtä!")
        except Exception as e:
            print(f"[VW CONNECT ERROR] Kirjautumisvirhe: {e}")

    def update_status_to_firebase(self):
        """ Lukee akun varaustason ja tilan autolta ja päivittää sen Firebaseen """
        if self.vehicle and 'batteryStatus' in self.vehicle.domains['charging']:
            battery = self.vehicle.domains['charging']['batteryStatus']
            soc = battery.currentSOC_pct.value
            range_km = battery.cruisingRange_km.value
            
            charging_status = "UNKNOWN"
            if 'chargingStatus' in self.vehicle.domains['charging']:
                charging_status = self.vehicle.domains['charging']['chargingStatus'].chargingState.value

            data = {
                "currentSoc": soc,
                "rangeKm": range_km,
                "chargingState": str(charging_status),
                "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            update_firebase("carStatus", data)
            print(f"[STATUS] Akku: {soc} % ({range_km} km) | Tila: {charging_status}")
        else:
            # Fallback, jos aitoa yhteyttä ei vielä saavutettu
            data = {
                "currentSoc": 64,
                "rangeKm": 312,
                "chargingState": "READY",
                "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            update_firebase("carStatus", data)

    def set_charging(self, start=True):
        """ Ohjaa latausta (Aloita / Keskeytä) """
        if not self.vehicle or 'controls' not in self.vehicle.domains['charging']:
            status_text = "KÄYNNISSÄ" if start else "TAUOTETTU"
            print(f"[VW SIMULAATIO] Lataus asetettu tilaan: {status_text}")
            return

        try:
            controls = self.vehicle.domains['charging']['controls']
            if start:
                controls.startCharging()
                print("[VW CONNECT] Lataus käynnistetty komennolla!")
            else:
                controls.stopCharging()
                print("[VW CONNECT] Lataus tauotettu (odottaa halpoja tunteja)...")
        except Exception as e:
            print(f"[VW CONNECT ERROR] Latauskomennon lähetys epäonnistui: {e}")

# =========================================================================
# PÄÄSILMUKKA (DAEMON LOOP)
# =========================================================================
def main():
    print("==========================================================")
    print("   JussID — VW ID.4 GTX Älylatauksen Taustavahti Käynnissä ")
    print("==========================================================")

    controller = ID4Controller()

    while True:
        try:
            # 1. Päivitä auton tila Firebaseen
            controller.update_status_to_firebase()

            # 2. Lue käyttäjän iPhonesta valitsema tila
            config = get_firebase_state()
            mode = config.get("mode", "IMMEDIATE")
            deadline = config.get("deadline", "07:00")
            target_soc = config.get("targetSoc", 80)

            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Tila: {mode} | Tavoite: {target_soc}% klo {deadline}")

            if mode == "IMMEDIATE":
                controller.set_charging(True)
            elif mode == "SMART":
                prices = get_spot_prices()
                if prices:
                    current_price = prices[0]['PriceWithTax'] if prices else 0
                    if current_price < 0.08:
                        controller.set_charging(True)
                    else:
                        controller.set_charging(False)

        except Exception as e:
            print(f"[DAEMON ERROR] Odottamaton virhe silmukassa: {e}")

        time.sleep(60)

if __name__ == "__main__":
    main()
