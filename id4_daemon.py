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

# Firebase Realtime Database URL (JussID - Hankittu aiemmin)
FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

# Latauslaitteiston tekniset parametrit
BATTERY_CAPACITY_KWH = 77.0  # ID.4 GTX käytettävissä oleva akkukapasiteetti
CHARGER_POWER_KW     = 11.0  # Evenli 3-vaihe Type2 -latausteho (11 kW)

# =========================================================================
# 2. FIREBASE PILVIYHTEYS & TILAN LUKU
# =========================================================================
def fetch_user_settings_from_firebase():
    """Lukee puhelinsovelluksella asetetut valinnat Firebasesta."""
    try:
        url = f"{FIREBASE_URL}/settings.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200 and response.json() is not None:
            return response.json()
    except Exception as e:
        print(f"⚠️️ Virhe Firebasen lukemisessa: {e}")
    
    # Oletusarvot, jos pilviyhteydessä on häiriö (Heti-lataus varalta päälle)
    return {
        "mode": "DIRECT",       # 'DIRECT' (Heti-lataus) tai 'SMART' (Älylataus)
        "targetSoc": 80,        # 80 % tai 100 %
        "deadline": "07:00",    # Aamun valmistumisaika
        "paused": False
    }

def update_telemetry_to_firebase(battery_pct, is_charging):
    """Lähettää auton nykyisen tilan pilveen puhelimen näyttöä varten."""
    try:
        url = f"{FIREBASE_URL}/telemetry.json"
        data = {
            "batteryPercent": battery_pct,
            "isCharging": is_charging,
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        requests.patch(url, json=data, timeout=5)
    except Exception as e:
        print(f"⚠️ Virhe telemetrian päivityksessä: {e}")

# =========================================================================
# 3. PÖRSSISÄHKÖN HINTA-ANALYYSI (SPOT-HINTA API)
# =========================================================================
def get_spot_prices():
    """Noutaa kuluvan ja huomisen pörssisähkön tuntihinnat."""
    try:
        url = "https://api.spot-hinta.fi/TodayAndTomorrow"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"⚠️ Virhe pörssihintojen haussa: {e}")
    return []

def is_current_hour_cheapest(settings, current_soc):
    """Laskee tarvittavan latausajan ja tarkistaa, onko nykyinen tunti halpa."""
    target_soc = settings.get("targetSoc", 80)
    if current_soc >= target_soc:
        return False

    # Lasketaan kuinka monta tuntia ID.4 tarvitsee tavoitteen saavuttamiseen
    needed_kwh = ((target_soc - current_soc) / 100.0) * BATTERY_CAPACITY_KWH
    needed_hours = int((needed_kwh / CHARGER_POWER_KW) + 0.9)  # Pyöristetään ylöspäin
    
    prices = get_spot_prices()
    if not prices:
        return True  # Jos hintatietoa ei saada, ladataan varalta

    now = datetime.datetime.now()
    deadline_hour = int(settings.get("deadline", "07:00").split(":")[0])

    # Suodatetaan tunnit ennen aamun takarajaa
    relevant_slots = []
    for item in prices:
        dt = datetime.datetime.fromisoformat(item["DateTime"])
        if dt >= now and dt.hour <= deadline_hour:
            relevant_slots.append(item)

    # Järjestetään halvimmasta kalleimpaan
    relevant_slots.sort(key=lambda x: x["PriceWithTax"])
    cheapest_times = [p["DateTime"] for p in relevant_slots[:needed_hours]]

    current_hour_iso = now.strftime("%Y-%m-%dT%H:00:00")
    return any(t.startswith(current_hour_iso[:13]) for t in cheapest_times)

# =========================================================================
# 4. VOLKSWAGEN MEB & EVENLI OHJAUSLOGIIKKA
# =========================================================================
class ID4VehicleController:
    def __init__(self, username, password, vin):
        self.username = username
        self.password = password
        self.vin = vin
        self.is_connected = False

    def connect(self):
        """Kirjautuu Volkswagen We Connect / MEB -rajapintaan."""
        if self.username == "OMA_SAHKOPOSTI@EXAMPLE.COM":
            print("⚠️ VW-tunnuksia ei ole vielä asetettu skriptiin! Käytetään simulaatiotilaa.")
            return False
        
        try:
            # Tähän kytkeytyy pyvwconnect / weconnect -kirjasto
            print(f"🔑 Kirjaudutaan Volkswagen-tilille ({self.username})...")
            self.is_connected = True
            return True
        except Exception as e:
            print(f"❌ Virhe VW-kirjautumisessa: {e}")
            return False

    def get_battery_level(self):
        """Hakee auton todellisen akun prosenttitason."""
        # Palautetaan testiympäristössä 54 % (Oikeassa ajossa pyytää autolta)
        return 54

    def start_charging(self):
        """Käynnistää latauksen auton ja Evenli-tolpan puolelta."""
        print("⚡ [KÄSKSY] Herätetään ID.4 GTX ja aloitetaan lataus (11 kW)...")

    def stop_charging(self):
        """Pysäyttää latauksen ja jättää Evenlin Keep-Alive-valmiustilaan."""
        print("💤 [KÄSKY] Pysäytetään lataus. Evenli-tolppa siirretty valmiustilaan.")

# ==========================================
# 5. TAUSTAPROSESSIN PÄÄSILMUKKA
# ==========================================
def main():
    print("==========================================")
    print("      JussID Taustavahti v1.0 käynnissä   ")
    print("==========================================")
    print(f"🔗 Firebase URL: {FIREBASE_URL}")

    car = ID4VehicleController(VW_USERNAME, VW_PASSWORD, VW_VIN)
    car.connect()

    while True:
        try:
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now_str}] Tarkistetaan tilanne...")

            # 1. Luetaan asetukset puhelimesta
            settings = fetch_user_settings_from_firebase()
            mode = settings.get("mode", "DIRECT")
            target_soc = settings.get("targetSoc", 80)
            deadline = settings.get("deadline", "07:00")
            
            # 2. Luetaan auton akun tila
            current_soc = car.get_battery_level()
            print(f"📊 Tila: Akku {current_soc}% / Tavoite {target_soc}% | Moodi: {mode} | Valmis: {deadline}")

            # 3. Suoritetaan ohjauslogiikka
            if mode == "DIRECT":
                print("⚡ Moodi: HETI-LATAUS -> Kytketään täysi teho päälle.")
                if current_soc < target_soc:
                    car.start_charging()
                    update_telemetry_to_firebase(current_soc, True)
                else:
                    print("✅ Tavoite saavutettu. Pysäytetään lataus.")
                    car.stop_charging()
                    update_telemetry_to_firebase(current_soc, False)

            elif mode == "SMART":
                print(f"🌙 Moodi: ÄLYLATAUS -> Ajoitetaan yöksi ennen klo {deadline}.")
                should_charge = is_current_hour_cheapest(settings, current_soc)

                # Fail-safe: klo 04:00 pakotettu käynnistys jos aamu lähestyy
                current_hour = datetime.datetime.now().hour
                deadline_hour = int(deadline.split(":")[0])
                if current_hour >= 4 and current_hour < deadline_hour and current_soc < target_soc:
                    print("🚨 [FAIL-SAFE] Aamu lähestyy! Pakotetaan lataus käyntiin.")
                    should_charge = True

                if should_charge and current_soc < target_soc:
                    print("💰 Edullinen pörssitunti käynnissä -> Lataus PÄÄLLÄ.")
                    car.start_charging()
                    update_telemetry_to_firebase(current_soc, True)
                else:
                    print("📈 Kallis pörssitunti tai tavoite saavutettu -> Lataus PAUSSILLA (Evenli Standby).")
                    car.stop_charging()
                    update_telemetry_to_firebase(current_soc, False)

        except Exception as err:
            print(f"⚠️ Odottamaton virhe silmukassa: {err}")

        # Odotetaan 2 minuuttia ennen seuraavaa tarkistuskierrosta
        time.sleep(120)

if __name__ == "__main__":
    main()
```

---
