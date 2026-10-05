#!/usr/bin/env python3
"""
=============================================================================
 JussID – Python Taustapalvelu (Daemon v1.0)
 ID.4 GTX & Evenli Smart Charging Controller
=============================================================================
 Tämä skripti toimii JussID-järjestelmän taustamoottorina:
 1. Kuuntelee iPhonella tehtyjä valintoja Firebase-pilvestä (jussid-ede86).
 2. Noutaa Suomen pörssisähkön tuntihinnat (Spot-hinta API).
 3. Pitää Evenli-lataustolpan kättelyn hengissä (Keep-Alive).
 4. Ohjaa Volkswagen ID.4 GTX -auton latausta yöllä edullisimmilla tunneilla.
=============================================================================
"""

import time
import datetime
import requests

# =========================================================================
# 1. KÄYTTÄJÄN ASETUKSET (TÄYTÄ OMAT VOLKSWAGEN-TUNNUKSET TÄHÄN)
# =========================================================================
VW_USERNAME = "jussi.parikka@gmail.com"  # Syötä VW ID -sähköpostisi
VW_PASSWORD = "R0manialainen2025"          # Syötä VW ID -salasanasi
VW_VIN      = "WVGZZZE2ZNP017960"           # Syötä autosi VIN-koodi

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

# Pyöritetään valepalvelinta omassa taustasäikeessään
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
            # Simulaatio-fallback, jos aitoa yhteyttä ei ole määritetty
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
            print(f"[VW SIMULAATIO] Lataus asetettu tilaan: {'K
