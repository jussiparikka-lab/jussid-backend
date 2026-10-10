#!/usr/bin/env python3
"""
=============================================================================
 JussID – Python Taustapalvelu (Daemon v2.0)
 ID.4 GTX & Evenli Smart Charging Controller
=============================================================================
"""

import os
import time
import datetime
import threading
import requests
from flask import Flask

# Korjattu weconnect-kirjaston tuonti (tukee sekä moduulia että suoraa luokkaa)
try:
    from weconnect import weconnect
    WeConnect = weconnect.WeConnect
    WE_CONNECT_AVAILABLE = True
except (ImportError, AttributeError):
    try:
        from weconnect.weconnect import WeConnect
        WE_CONNECT_AVAILABLE = True
    except (ImportError, AttributeError):
        WE_CONNECT_AVAILABLE = False

# =========================================================================
# FIREBASE & VW CONNECT TUNNUKSET (TÄYTÄ OMASI TÄHÄN)
# =========================================================================
FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

VW_USERNAME = "jussi.parikka@gmail.com"  # Syötä VW ID -sähköpostisi
VW_PASSWORD = "R0manialainen2025"          # Syötä VW ID -salasanasi
VW_VIN      = "WVGZZZE2ZNP017960"           # Syötä autosi VIN-koodi

# =========================================================================
# PIENI VALEPALVELIN (Pitää Render Web Servicen käynnissä 24/7)
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
        url = "https://api.spot-hinta.fi/TodayAndTomorrow"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return sorted(res.json(), key=lambda x: x.get('DateTime', ''))
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
    return {"mode": "SMART", "deadline": "07:00", "targetSoc": 80}

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
            print("[VW CONNECT ERROR] 'weconnect'-kirjastoa ei löytynyt ympäristöstä!")
            return

        if self.weconnect is None:
            try:
                print(f"[VW CONNECT] Kirjaudutaan Volkswagen-palvelimelle ({VW_USERNAME})...")
                self.weconnect = WeConnect(
                    username=VW_USERNAME,
                    password=VW_PASSWORD,
                    updateAfterLogin=True,
                    loginOnInit=True
                )
                if VW_VIN in self.weconnect.vehicles:
                    self.vehicle = self.weconnect.vehicles[VW_VIN]
                    name = getattr(self.vehicle, 'nickname', None) or VW_VIN
                    print(f"✅ [VW CONNECT] Yhdistetty autoon onnistuneesti: {name}")
                else:
                    found_vins = list(self.weconnect.vehicles.keys())
                    print(f"⚠️ [VW CONNECT ERROR] VIN {VW_VIN} ei löytynyt! Tilin autot: {found_vins}")
            except Exception as e:
                print(f"❌ [VW CONNECT ERROR] Kirjautumisvirhe: {e}")

    def update_status_to_firebase(self):
        """ Hakee tuoreen akkutiedon autolta ja päivittää sen Firebaseen """
        # Jos yhteys puuttuu, kokeillaan yhdistää uudelleen
        if not self.vehicle:
            self.connect()

        # Päivitetään tiedot VW:n palvelimilta
        if self.weconnect:
            try:
                self.weconnect.update()
            except Exception as e:
                print(f"[VW UPDATE ERROR] Auton tietojen päivitys epäonnistui: {e}")

        # Luetaan akku ja tila
        if self.vehicle and 'charging' in self.vehicle.domains:
            charging_domain = self.vehicle.domains['charging']
            
            soc = None
            range_km = None
            if 'batteryStatus' in charging_domain:
                b_stat = charging_domain['batteryStatus']
                soc = getattr(b_stat.currentSOC_pct, 'value', None)
                range_km = getattr(b_stat.cruisingRange_km, 'value', None)

            charging_status = "READY"
            if 'chargingStatus' in charging_domain:
                c_stat = charging_domain['chargingStatus']
                raw_state = getattr(c_stat.chargingState, 'value', None)
                charging_status = str(raw_state) if raw_state else "READY"

            if soc is not None:
                data = {
                    "currentSoc": int(soc),
                    "rangeKm": int(range_km) if range_km else int(soc * 4.6),
                    "chargingState": charging_status,
                    "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                update_firebase("carStatus", data)
                print(f"📊 [STATUS] Akku: {soc} % ({range_km} km) | Tila: {charging_status}")
                return

        print("⚠️ [STATUS] Oikeaa akkutietoa ei saatu, tarkista VW-tunnukset ja kirjautuminen.")

    def set_charging(self, start=True):
        """ Ohjaa latausta (Aloita / Keskeytä) """
        if not self.vehicle or 'charging' not in self.vehicle.domains:
            return

        charging_domain = self.vehicle.domains['charging']
        if 'controls' in charging_domain:
            controls = charging_domain['controls']
            try:
                if start and hasattr(controls, 'startCharging'):
                    controls.startCharging()
                    print("⚡ [VW CONNECT] Lataus käynnistetty autolta!")
                elif not start and hasattr(controls, 'stopCharging'):
                    controls.stopCharging()
                    print("💤 [VW CONNECT] Lataus tauotettu autolta!")
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
            # 1. Päivitä auton tila autolta -> Firebaseen
            controller.update_status_to_firebase()

            # 2. Lue käyttäjän puhelimesta valitsema tila
            config = get_firebase_state()
            mode = config.get("mode", "SMART")
            deadline = config.get("deadline", "07:00")
            target_soc = config.get("targetSoc", 80)

            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Tila: {mode} | Tavoite: {target_soc}% klo {deadline}")

            if mode == "IMMEDIATE":
                controller.set_charging(True)
            elif mode == "SMART":
                prices = get_spot_prices()
                if prices:
                    # Yksinkertainen hintaohjaus: ladataan alle 8 c/kWh tai aamuyöllä
                    current_price = prices[0].get('PriceWithTax', 0.0)
                    now_hour = datetime.datetime.now().hour
                    if current_price < 0.08 or (1 <= now_hour <= 5):
                        controller.set_charging(True)
                    else:
                        controller.set_charging(False)

        except Exception as e:
            print(f"[DAEMON ERROR] Odottamaton virhe silmukassa: {e}")

        # Tarkistus 60 sekunnin välein
        time.sleep(60)

if __name__ == "__main__":
    main()
