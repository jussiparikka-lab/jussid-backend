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
import math
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
# FIREBASE & VW CONNECT TUNNUKSET
# =========================================================================
FIREBASE_URL = "https://jussid-ede86-default-rtdb.europe-west1.firebasedatabase.app"

VW_USERNAME = "jussi.parikka@gmail.com"
VW_PASSWORD = "OMA_SALASANA_TAHAN"          # Varmista oma salasanasi
VW_VIN      = "WVGZZZE2ZNP017960"

BATTERY_CAPACITY_KWH = 77.0  # ID.4 GTX nettokapasiteetti
CHARGING_POWER_KW = 11.0     # Evenli Type 2 kolmivaihe 16A (11 kW)

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
    return {"mode": "IMMEDIATE", "deadline": "07:00", "targetSoc": 80}

def calculate_smart_schedule(current_soc, target_soc, deadline_str, spot_prices):
    """
    Laskee tarvittavan lataustuntimäärän (11 kW) ja valitsee
    halvimmat spot-tunnit ennen annettua deadline-aikaa.
    Palauttaa: (should_charge_now, reason, selected_hours_list)
    """
    now = datetime.datetime.now()
    
    # Jos akussa on jo riittävästi virtaa
    if current_soc >= target_soc:
        return False, f"Akku on jo saavuttanut tavoitteen ({current_soc}% >= {target_soc}%)", []

    # 1. Lasketaan montako kWh ja montako täyttä tuntia tarvitaan 11 kW teholla
    needed_soc = target_soc - current_soc
    needed_kwh = (needed_soc / 100.0) * BATTERY_CAPACITY_KWH
    # Lisätään n. 10 % lataushäviöitä ja pyöristetään ylöspäin tunneiksi
    needed_hours = max(1, math.ceil((needed_kwh * 1.10) / CHARGING_POWER_KW))

    # 2. Määritetään määräaika (deadline)
    try:
        d_hour, d_min = map(int, deadline_str.split(':'))
    except Exception:
        d_hour, d_min = 7, 0

    deadline_dt = now.replace(hour=d_hour, minute=d_min, second=0, microsecond=0)
    # Jos deadline on jo mennyt tältä päivältä (esim. kello on 21 ja deadline on 07), se on huomisaamuna
    if deadline_dt <= now:
        deadline_dt += datetime.timedelta(days=1)

    # 3. Suodatetaan spot-hinnoista ne tunnit, jotka ovat NYT ja DEADLINEN välissä
    candidate_slots = []
    for item in spot_prices:
        dt_str = item.get('DateTime')
        if not dt_str:
            continue
        try:
            # API palauttaa tyypillisesti ISO-muodossa esim. 2026-10-10T02:00:00+03:00
            slot_time = datetime.datetime.fromisoformat(dt_str)
            # Poistetaan aikavyöhyketieto vertailua varten jos local
            if slot_time.tzinfo:
                slot_time = slot_time.replace(tzinfo=None)
            
            if now <= slot_time < deadline_dt:
                candidate_slots.append({
                    "time": slot_time,
                    "price": item.get('PriceWithTax', 0.0),
                    "iso": dt_str
                })
        except Exception:
            continue

    # Jos spot-tietoja ei saada tarpeeksi, varalogiikka: ladataan aamuyöllä 01-05 tai heti
    if not candidate_slots:
        time_to_deadline_h = (deadline_dt - now).total_seconds() / 3600.0
        if time_to_deadline_h <= needed_hours:
            return True, f"Varatila: Aikaa jäljellä vain {time_to_deadline_h:.1f}h, pakotetaan lataus päälle", []
        return False, "Odotetaan yötä (spot-data puuttuu)", []

    # 4. Jos aikaa deadlineen on vähemmän tai tasan tarvittava määrä tunteja -> pakotetaan lataus heti
    if len(candidate_slots) <= needed_hours:
        return True, f"Kaikki jäljellä olevat {len(candidate_slots)} tuntia tarvitaan tavoitteeseen pääsyyn", candidate_slots

    # 5. Järjestetään ehdokastunnit hinnan mukaan halvimmasta kalleimpaan ja valitaan halvimmat N tuntia
    sorted_by_price = sorted(candidate_slots, key=lambda x: x['price'])
    cheapest_slots = sorted_by_price[:needed_hours]
    cheapest_times = [s['time'] for s in cheapest_slots]

    # Tarkistetaan, osuuko kuluva tunti halvimpiin valittuihin tunteihin
    current_hour_dt = now.replace(minute=0, second=0, microsecond=0)
    is_current_slot_chosen = any(abs((t - current_hour_dt).total_seconds()) < 1800 for t in cheapest_times)

    hours_list_str = ", ".join([t.strftime('%H:00') for t in sorted(cheapest_times)])
    if is_current_slot_chosen:
        return True, f"Kuluva tunti on valittu halvoista tunneista ({hours_list_str})", cheapest_slots
    else:
        return False, f"Odottaa edullisempia tunteja ({hours_list_str}), tarve {needed_hours} h", cheapest_slots

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
        if not self.vehicle:
            self.connect()

        if self.weconnect:
            try:
                self.weconnect.update()
            except Exception as e:
                print(f"[VW UPDATE ERROR] Auton tietojen päivitys epäonnistui: {e}")

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
                return int(soc)

        print("⚠️ [STATUS] Oikeaa akkutietoa ei saatu, tarkista VW-tunnukset ja kirjautuminen.")
        return None

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
                    print("⚡ [VW CONNECT] Lataus komennus: KÄYNNISSÄ (11 kW)")
                elif not start and hasattr(controls, 'stopCharging'):
                    controls.stopCharging()
                    print("💤 [VW CONNECT] Lataus komennus: KESKEYTETTY (Odottaa halpaa yötä)")
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
            # 1. Päivitä auton todellinen tila autolta -> Firebaseen
            current_soc = controller.update_status_to_firebase()
            if current_soc is None:
                current_soc = 60  # Varmuuslukema jos yhteys tilapäisesti poikki

            # 2. Lue käyttäjän puhelimesta valitsema tila
            config = get_firebase_state()
            mode = config.get("mode", "IMMEDIATE") # Oletus: Heti-lataus, ellei puhelimesta valita SMART
            deadline = config.get("deadline", "07:00")
            target_soc = config.get("targetSoc", 80)

            now_str = datetime.datetime.now().strftime('%H:%M:%S')

            # VAIHE A: Käyttäjä ei ole valinnut Älylatausta tai on valinnut Heti-latauksen
            if mode == "IMMEDIATE":
                print(f"[{now_str}] Tila: HETI-LATAUS | Akku: {current_soc}% -> Ladataan täydellä teholla!")
                controller.set_charging(True)

            # VAIHE B: ÄLYLATAUS (Valitaan halvimmat tunnit ja varmistetaan valmistuminen deadlineen)
            elif mode == "SMART":
                spot_prices = get_spot_prices()
                should_charge, reason, _ = calculate_smart_schedule(
                    current_soc=current_soc,
                    target_soc=target_soc,
                    deadline_str=deadline,
                    spot_prices=spot_prices
                )
                print(f"[{now_str}] ÄLYLATAUS: {reason}")
                controller.set_charging(should_charge)

        except Exception as e:
            print(f"[DAEMON ERROR] Odottamaton virhe silmukassa: {e}")

        # Tarkistus 60 sekunnin välein
        time.sleep(60)

if __name__ == "__main__":
    main()
