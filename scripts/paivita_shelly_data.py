#!/usr/bin/env python3
"""
Shelly EM / 3EM — inkrementaalinen kulutusdatan haku
====================================================
- Hakee paikallisen HTTP-rajapinnan /emdata/<channel>/data.csv
- Tallentaa kuukausittain: YYYY-MM_lokaali_<Nimi>_historia.csv (1 min)
- Aggregoi: YYYY-MM_15m_lokaali_<Nimi>_historia.csv
- PING pre-flight ennen HTTP-hakuja
- Laitteet: config/devices.json (ei salaisuuksia repossä)
"""
from __future__ import annotations

import csv
import glob
import os
import platform
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from config_loader import DATA_HAKEMISTO, fetch_mode, lataa_config, laitteet_dict

KANAVA_OLETUS = 0
TUNTI_S = 3600
CHUNK_S = 6 * 3600          # full-tila: max 6 h / HTTP-kutsu (välttää Connection reset)
HOURLY_TAUKO_S = 2
CHUNK_TAUKO_S = 0.4
LAITE_HISTORIA_PAIVIA = 10

NEW_HEADER = [
    "timestamp",
    "a_total_act_energy", "a_avg_active_power", "a_max_act_power", "a_min_act_power",
    "a_avg_current", "a_avg_voltage",
    "b_total_act_energy", "b_avg_active_power", "b_max_act_power", "b_min_act_power",
    "b_avg_current", "b_avg_voltage",
    "c_total_act_energy", "c_avg_active_power", "c_max_act_power", "c_min_act_power",
    "c_avg_current", "c_avg_voltage",
    "n_avg_current",
]


def lataa_url(url: str, timeout: int = 120, uudelleenyritykset: int = 2):
    for yritys in range(uudelleenyritykset + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if yritys < uudelleenyritykset:
                print(f"    ↻  Uudelleenyritys ({yritys + 2}/{uudelleenyritykset + 1})...")
                time.sleep(1.5)
                continue
            print(f"    ⚠️  Yhteysvirhe: {e}")
            return None


def parsii_data_rivit(raw_data: str) -> list[str]:
    return [
        r.replace("\r", "")
        for r in raw_data.splitlines()
        if r.strip() and not r.startswith("timestamp")
    ]


def transform_row(original_row: list[str]):
    """52-sarakkeinen Shelly-rivi → 20 saraketta."""
    r = original_row
    try:
        ts = r[0]
        a_e = float(r[1])
        b_e = float(r[17])
        c_e = float(r[33])
        return [
            ts,
            round(a_e, 4), round(a_e * 60, 2), r[7], r[8], r[16], r[13],
            round(b_e, 4), round(b_e * 60, 2), r[23], r[24], r[32], r[29],
            round(c_e, 4), round(c_e * 60, 2), r[39], r[40], r[48], r[45],
            r[51],
        ]
    except (ValueError, IndexError):
        return None


def paivita_15min_tiedosto(file_1m: str, file_15m: str) -> None:
    if not os.path.exists(file_1m):
        return

    rows_1m = []
    with open(file_1m, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for r in reader:
            rows_1m.append(r)

    if not rows_1m:
        return

    blocks: dict[int, list] = {}
    for r in rows_1m:
        ts = int(r[0])
        b_ts = (ts // 900) * 900
        blocks.setdefault(b_ts, []).append(r)

    agg_rows = []
    for b_ts in sorted(blocks.keys()):
        b_rows = blocks[b_ts]
        try:
            row = [str(b_ts)]
            for i in range(1, 19):
                vals = [float(r[i]) for r in b_rows if r[i]]
                if not vals:
                    row.append("")
                    continue
                if i in (1, 7, 13):
                    row.append(round(sum(vals), 4))
                elif i in (3, 9, 15):
                    row.append(round(max(vals), 2))
                elif i in (4, 10, 16):
                    row.append(round(min(vals), 2))
                else:
                    row.append(round(sum(vals) / len(vals), 3))
            n_vals = [float(r[19]) for r in b_rows if r[19]]
            row.append(round(sum(n_vals) / len(n_vals), 3) if n_vals else "")
            agg_rows.append(row)
        except Exception:
            continue

    with open(file_15m, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(NEW_HEADER)
        writer.writerows(agg_rows)


def tulosta_paivitykset(nimi: str, kokonaispaivitykset: dict) -> None:
    for month, maara in sorted(kokonaispaivitykset.items()):
        tiedosto_1m = os.path.join(DATA_HAKEMISTO, f"{month}_lokaali_{nimi}_historia.csv")
        tiedosto_15m = os.path.join(DATA_HAKEMISTO, f"{month}_15m_lokaali_{nimi}_historia.csv")
        print(f"  ✅ {month}: Lisättiin {maara} uutta riviä ({nimi}).")
        paivita_15min_tiedosto(tiedosto_1m, tiedosto_15m)
        print(f"  📊 {month}: 15min kooste päivitetty.")


def lue_viimeisin_timestamp(tiedosto: str):
    if not os.path.exists(tiedosto):
        return None
    viimeisin = None
    with open(tiedosto, encoding="utf-8") as f:
        for rivi in f:
            try:
                viimeisin = int(rivi.split(",")[0])
            except ValueError:
                continue
    return viimeisin


def lue_viimeisin_timestamp_laitteelta(nimi: str):
    viimeisin = None
    for tiedosto in glob.glob(os.path.join(DATA_HAKEMISTO, f"*_lokaali_{nimi}_historia.csv")):
        ts = lue_viimeisin_timestamp(tiedosto)
        if ts is not None and (viimeisin is None or ts > viimeisin):
            viimeisin = ts
    return viimeisin


def lue_timestampit_laitteelta(nimi: str) -> list[int]:
    ts_list = []
    for tiedosto in glob.glob(os.path.join(DATA_HAKEMISTO, f"*_lokaali_{nimi}_historia.csv")):
        with open(tiedosto, encoding="utf-8") as f:
            for rivi in f:
                try:
                    ts_list.append(int(rivi.split(",")[0]))
                except ValueError:
                    continue
    return sorted(ts_list)


def etsi_puuttuvat_tunnit(nimi: str, loppu_ts: int) -> list[int]:
    vanhin_mahdollinen = loppu_ts - LAITE_HISTORIA_PAIVIA * 86400
    ts_list = [t for t in lue_timestampit_laitteelta(nimi) if t >= vanhin_mahdollinen]

    if not ts_list:
        alku = (vanhin_mahdollinen // TUNTI_S) * TUNTI_S
    else:
        alku = (ts_list[0] // TUNTI_S) * TUNTI_S

    ts_set = set(ts_list)
    puuttuvat = []
    t = alku
    while t < loppu_ts:
        if not all((t + m * 60) in ts_set for m in range(60)):
            puuttuvat.append(t)
        t += TUNTI_S
    return puuttuvat


def tallenna_uudet_rivit(nimi: str, data_rivit: list[str]) -> dict:
    kuukausittain: dict[str, list] = {}
    for r_str in data_rivit:
        r = r_str.split(",")
        try:
            ts = int(r[0])
            month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
            kuukausittain.setdefault(month, []).append(r)
        except (ValueError, IndexError):
            continue

    paivitetyt: dict[str, int] = {}
    for month, rivit in kuukausittain.items():
        tiedosto_1m = os.path.join(DATA_HAKEMISTO, f"{month}_lokaali_{nimi}_historia.csv")
        viimeisin_ts = lue_viimeisin_timestamp(tiedosto_1m)

        uudet = []
        for r in rivit:
            ts = int(r[0])
            if viimeisin_ts is None or ts > viimeisin_ts:
                t_row = transform_row(r)
                if t_row:
                    uudet.append(t_row)

        if uudet:
            file_exists = os.path.exists(tiedosto_1m)
            with open(tiedosto_1m, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(NEW_HEADER)
                writer.writerows(uudet)
            paivitetyt[month] = paivitetyt.get(month, 0) + len(uudet)
    return paivitetyt


def paivita_laite_full(nimi: str, ip: str, channel: int) -> bool:
    """Hakee dataa 6 h pätkissä viimeisestä tallennetusta pisteestä eteenpäin.

    Yksi iso /data.csv-kaato kaatuu helposti (Connection reset) → aikavälihaut.
    """
    nyt = int(time.time())
    viimeisin = lue_viimeisin_timestamp_laitteelta(nimi)
    if viimeisin is None:
        alku = nyt - LAITE_HISTORIA_PAIVIA * 86400
        print(f"    📥 Ensimmäinen haku: ~{LAITE_HISTORIA_PAIVIA} pv ({CHUNK_S // 3600} h pätkinä)")
    else:
        alku = viimeisin + 60
        if alku >= nyt:
            print("    ℹ️  Ei uutta dataa (ajan tasalla).")
            return True
        print(f"    📥 Jatketaan timestampista {viimeisin}")

    kokonaispaivitykset: dict[str, int] = {}
    t = (alku // 60) * 60
    chunk_n = 0
    while t < nyt:
        end = min(t + CHUNK_S, nyt)
        chunk_n += 1
        url = (
            f"http://{ip}/emdata/{channel}/data.csv"
            f"?add_keys=true&ts={t}&end_ts={end}"
        )
        raw_data = lataa_url(url, timeout=180, uudelleenyritykset=3)
        if raw_data is None:
            return False

        data_rivit = parsii_data_rivit(raw_data)
        if data_rivit:
            for month, maara in tallenna_uudet_rivit(nimi, data_rivit).items():
                kokonaispaivitykset[month] = kokonaispaivitykset.get(month, 0) + maara

        t = end
        if t < nyt:
            time.sleep(CHUNK_TAUKO_S)

    if kokonaispaivitykset:
        tulosta_paivitykset(nimi, kokonaispaivitykset)
    else:
        print(f"    ℹ️  Ei uutta dataa ({chunk_n} pätkää tarkistettu).")
    return True


def paivita_laite_hourly(nimi: str, ip: str, channel: int) -> bool:
    nyt = int(time.time())
    haettavat = etsi_puuttuvat_tunnit(nimi, nyt)

    if not haettavat:
        print("    ℹ️  Ei puuttuvia tunteja (data ajan tasalla).")
        return True

    print(f"    🔍 Löytyi {len(haettavat)} puutteellista tuntia")
    kokonaispaivitykset: dict[str, int] = {}

    for i, tunti_ts in enumerate(haettavat):
        end_ts = tunti_ts + TUNTI_S
        aika = datetime.fromtimestamp(tunti_ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        print(f"    ⏱  Haetaan tunti {aika} UTC...")

        url = (
            f"http://{ip}/emdata/{channel}/data.csv"
            f"?add_keys=true&ts={tunti_ts}&end_ts={end_ts}"
        )
        raw_data = lataa_url(url, timeout=300, uudelleenyritykset=2)
        if raw_data is None:
            return False

        data_rivit = parsii_data_rivit(raw_data)
        if data_rivit:
            for month, maara in tallenna_uudet_rivit(nimi, data_rivit).items():
                kokonaispaivitykset[month] = kokonaispaivitykset.get(month, 0) + maara
            print(f"       → {len(data_rivit)} riviä")
        else:
            print("       → ei dataa laitteella")

        if i < len(haettavat) - 1:
            time.sleep(HOURLY_TAUKO_S)

    if kokonaispaivitykset:
        tulosta_paivitykset(nimi, kokonaispaivitykset)
    return True


def paivita_laite(cfg: dict, nimi: str, ip: str) -> bool:
    channel = int(cfg.get("em_channel", KANAVA_OLETUS))
    mode = fetch_mode(cfg, nimi)
    if mode == "hourly":
        return paivita_laite_hourly(nimi, ip, channel)
    return paivita_laite_full(nimi, ip, channel)


def ping_laite(ip: str, timeout_s: int = 2) -> bool:
    """macOS: -W millisekunteina; Linux: -W sekunteina."""
    system = platform.system()
    if system == "Darwin":
        wait_arg = str(timeout_s * 1000)
    else:
        wait_arg = str(timeout_s)

    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", wait_arg, ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s + 1,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def tarkista_yhteydet(laitteet: dict[str, str]) -> bool:
    print("\n🔍 Tarkistetaan verkkoyhteydet (PING)...")
    kaikki_ok = True
    for nimi, ip in laitteet.items():
        if ping_laite(ip):
            print(f"  ✅ {nimi} ({ip}) — tavoitettavissa")
        else:
            print(f"  ❌ {nimi} ({ip}) — EI VASTAA")
            kaikki_ok = False
    return kaikki_ok


def main() -> None:
    cfg = lataa_config()
    laitteet = laitteet_dict(cfg)

    print("═" * 55)
    print("  Shelly EM — kulutusdatan haku & 15 min kooste")
    print(f"  Aika: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("═" * 55)

    if not tarkista_yhteydet(laitteet):
        print("\n❌ KRIITTINEN VIRHE: Yksi tai useampi Shelly-laite ei vastaa PING-tarkistukseen.")
        print("👉 Tarkista verkko (sama LAN/VLAN/VPN) ja config/devices.json IP-osoitteet.")
        sys.exit(1)

    for nimi, ip in laitteet.items():
        print(f"\n📡 {nimi} ({ip})")
        if not paivita_laite(cfg, nimi, ip):
            print(f"\n❌ KRIITTINEN VIRHE: Datan haku laitteesta {nimi} epäonnistui.")
            sys.exit(1)

    print("\n✅ Kaikki laitteet päivitetty!")


if __name__ == "__main__":
    main()
