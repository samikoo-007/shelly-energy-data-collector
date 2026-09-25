#!/usr/bin/env python3
"""
Shelly 3EM-63 Gen3 — inkrementaalinen kulutusdatan haku
=======================================================
Testattu malli: Shelly 3EM-63 Gen3 (S3EM-003CXCEU63), Gen 3, triphase.

- Hakee paikallisen HTTP-rajapinnan /emdata/<channel>/data.csv
- Tallentaa kaikki 52 natiivia saraketta kuukausittain:
  YYYY-MM_lokaali_<Nimi>_historia.csv (1 min)
- Aggregoi: YYYY-MM_15m_lokaali_<Nimi>_historia.csv (samat 52 saraketta)
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
SARAKKEITA = 52

# Shelly 3EM-63 Gen3 /emdata CSV (add_keys=true) — natiivit kentät sellaisenaan
NATIVE_HEADER = [
    "timestamp",
    "a_total_act_energy", "a_fund_act_energy", "a_total_act_ret_energy", "a_fund_act_ret_energy",
    "a_lag_react_energy", "a_lead_react_energy",
    "a_max_act_power", "a_min_act_power", "a_max_aprt_power", "a_min_aprt_power",
    "a_max_voltage", "a_min_voltage", "a_avg_voltage",
    "a_max_current", "a_min_current", "a_avg_current",
    "b_total_act_energy", "b_fund_act_energy", "b_total_act_ret_energy", "b_fund_act_ret_energy",
    "b_lag_react_energy", "b_lead_react_energy",
    "b_max_act_power", "b_min_act_power", "b_max_aprt_power", "b_min_aprt_power",
    "b_max_voltage", "b_min_voltage", "b_avg_voltage",
    "b_max_current", "b_min_current", "b_avg_current",
    "c_total_act_energy", "c_fund_act_energy", "c_total_act_ret_energy", "c_fund_act_ret_energy",
    "c_lag_react_energy", "c_lead_react_energy",
    "c_max_act_power", "c_min_act_power", "c_max_aprt_power", "c_min_aprt_power",
    "c_max_voltage", "c_min_voltage", "c_avg_voltage",
    "c_max_current", "c_min_current", "c_avg_current",
    "n_max_current", "n_min_current", "n_avg_current",
]


def _agg_mode(col_name: str) -> str:
    """Miten 15 min -kooste lasketaan natiivikentälle."""
    if col_name == "timestamp":
        return "ts"
    if "_max_" in col_name or col_name.startswith(("a_max", "b_max", "c_max", "n_max")):
        return "max"
    if "_min_" in col_name or col_name.startswith(("a_min", "b_min", "c_min", "n_min")):
        return "min"
    if "energy" in col_name:
        return "sum"
    return "avg"


AGG_MODES = [_agg_mode(c) for c in NATIVE_HEADER]


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


def normalisoi_rivi(original_row: list[str]):
    """Palauttaa Shellyn natiivin 52-sarakkeisen rivin tai None."""
    if len(original_row) < SARAKKEITA:
        return None
    r = original_row[:SARAKKEITA]
    try:
        int(r[0])
    except ValueError:
        return None
    return r


def varmista_natiivi_tiedosto(tiedosto_1m: str) -> bool:
    """Jos vanha 20-sarakkeinen tiedosto on olemassa, arkistoi se ennen uutta formaattia."""
    if not os.path.exists(tiedosto_1m):
        return True
    with open(tiedosto_1m, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
    if header == NATIVE_HEADER:
        return True
    bak = tiedosto_1m.replace(".csv", "_legacy_narrow.csv")
    n = 1
    while os.path.exists(bak):
        bak = tiedosto_1m.replace(".csv", f"_legacy_narrow_{n}.csv")
        n += 1
    os.replace(tiedosto_1m, bak)
    # Vanha 15min-kooste pois tieltä
    tiedosto_15m = tiedosto_1m.replace("_lokaali_", "_15m_lokaali_")
    if os.path.exists(tiedosto_15m):
        bak15 = tiedosto_15m.replace(".csv", "_legacy_narrow.csv")
        os.replace(tiedosto_15m, bak15)
    print(f"    ♻️  Vanha kapea CSV arkistoitu → {os.path.basename(bak)}")
    return True


def paivita_15min_tiedosto(file_1m: str, file_15m: str) -> None:
    if not os.path.exists(file_1m):
        return

    rows_1m = []
    with open(file_1m, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != NATIVE_HEADER:
            print(f"    ⚠️  Ohitetaan 15min-kooste (odottamaton header): {os.path.basename(file_1m)}")
            return
        for r in reader:
            if len(r) >= SARAKKEITA:
                rows_1m.append(r[:SARAKKEITA])

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
            row: list = [str(b_ts)]
            for i in range(1, SARAKKEITA):
                vals = []
                for r in b_rows:
                    try:
                        if r[i] != "":
                            vals.append(float(r[i]))
                    except (ValueError, IndexError):
                        continue
                if not vals:
                    row.append("")
                    continue
                mode = AGG_MODES[i]
                if mode == "sum":
                    row.append(round(sum(vals), 6))
                elif mode == "max":
                    row.append(round(max(vals), 4))
                elif mode == "min":
                    row.append(round(min(vals), 4))
                else:
                    row.append(round(sum(vals) / len(vals), 4))
            agg_rows.append(row)
        except Exception:
            continue

    with open(file_15m, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(NATIVE_HEADER)
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


def _1m_tiedostot(nimi: str) -> list[str]:
    """Kuukausittaiset 1 min -tiedostot (ei 15min / legacy)."""
    out = []
    for tiedosto in glob.glob(os.path.join(DATA_HAKEMISTO, f"*_lokaali_{nimi}_historia.csv")):
        base = os.path.basename(tiedosto)
        if "_15m_" in base or "_legacy_" in base:
            continue
        out.append(tiedosto)
    return out


def lue_viimeisin_timestamp_laitteelta(nimi: str):
    viimeisin = None
    for tiedosto in _1m_tiedostot(nimi):
        ts = lue_viimeisin_timestamp(tiedosto)
        if ts is not None and (viimeisin is None or ts > viimeisin):
            viimeisin = ts
    return viimeisin


def lue_timestampit_laitteelta(nimi: str) -> list[int]:
    ts_list = []
    for tiedosto in _1m_tiedostot(nimi):
        with open(tiedosto, encoding="utf-8") as f:
            for rivi in f:
                try:
                    ts_list.append(int(rivi.split(",")[0]))
                except ValueError:
                    continue
    return sorted(ts_list)


def arkistoi_legacy_laitteelta(nimi: str) -> None:
    """Arkistoi vanhat kapeat (≠ 52 natiivia) CSV:t ennen uutta hakua."""
    for tiedosto in _1m_tiedostot(nimi):
        varmista_natiivi_tiedosto(tiedosto)


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
        varmista_natiivi_tiedosto(tiedosto_1m)
        viimeisin_ts = lue_viimeisin_timestamp(tiedosto_1m)

        uudet = []
        for r in rivit:
            ts = int(r[0])
            if viimeisin_ts is None or ts > viimeisin_ts:
                t_row = normalisoi_rivi(r)
                if t_row:
                    uudet.append(t_row)

        if uudet:
            file_exists = os.path.exists(tiedosto_1m)
            with open(tiedosto_1m, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(NATIVE_HEADER)
                writer.writerows(uudet)
            paivitetyt[month] = paivitetyt.get(month, 0) + len(uudet)
    return paivitetyt


def paivita_laite_full(nimi: str, ip: str, channel: int) -> bool:
    """Hakee dataa 6 h pätkissä viimeisestä tallennetusta pisteestä eteenpäin.

    Yksi iso /data.csv-kaato kaatuu helposti (Connection reset) → aikavälihaut.
    """
    arkistoi_legacy_laitteelta(nimi)
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
    arkistoi_legacy_laitteelta(nimi)
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
    print("  Shelly 3EM-63 Gen3 — kulutusdatan haku & 15 min kooste")
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
