# Shelly Energy Data Collector

Kerää Shelly EM / 3EM -kulutusmittareiden historian paikallisesta verkosta CSV-tiedostoihin. Ei vaadi pilvitunnuksia: data haetaan suoraan laitteen IP-osoitteesta.

**Vaatimukset:** Python 3.9+, sama verkko (tai VPN) kuin Shelly-laitteet.

---

## Nopea aloitus

```bash
git clone <tämän-repon-osoite>
cd shelly-energy-data-collector

# 1) Kopioi laitekonfiguraatio
cp config/devices.example.json config/devices.json

# 2) Muokkaa IP-osoitteet ja nimet (katso alla)
nano config/devices.json   # tai avaa editorilla

# 3) Testaa yhteys ja hae data
python3 scripts/paivita_shelly_data.py
```

Onnistunut ajo tulostaa PING-tarkistuksen ja tallentaa CSV:t kansioon `data/`.

---

## 1. Selvitä Shellyn IP-osoite

Skripti tarvitsee kunkin mittarin **paikallisen IP:n** (ei Shelly Cloud -tiliä).

### Tapa A — Shelly-sovellus / web-UI
1. Avaa laitteen asetukset (Shelly app tai `http://<ip>` selaimessa).
2. Katso **Device information** → IP address.
3. Suositus: anna laitteelle **kiinteä IP** (DHCP-varaus reitittimessä), jotta osoite ei vaihdu.

### Tapa B — Reititin
Kirjaudu reitittimeen → Connected devices / DHCP-lista → etsi nimi kuten `Shelly3EM-...`.

### Tapa C — Komentorivi (macOS / Linux)
```bash
# Esimerkki: skannaa oman verkon aliverkkoa (vaihda 192.168.1 → oma verkko)
ping -c 1 192.168.1.10

# Kun IP löytyy, testaa Shellyn CSV-rajapinta:
curl -s "http://192.168.1.10/emdata/0/data.csv?add_keys=true" | head -n 2
```

Ensimmäisen rivin pitäisi alkaa `timestamp,a_total_act_energy,...`.

> **VLAN-huomio:** Monet koti-IoT-laitteet ovat erillisessä VLAN-verkossa. Aja skripti samasta verkosta tai VPN:n kautta. Jos PING epäonnistuu, kyse on yleensä verkosta — ei skriptistä.

---

## 2. Tallenna IP-osoitteet: `config/devices.json`

Repon mukana tulee vain **esimerkki**. Omat osoitteet menevät tiedostoon, jota **ei versionhallita**:

| Tiedosto | Rooli |
|---|---|
| `config/devices.example.json` | Mallipohja (repossa) |
| `config/devices.json` | Sinun laitteesi (gitignore — ei päädy Gitiin) |

```bash
cp config/devices.example.json config/devices.json
```

Muokkaa esimerkiksi näin:

```json
{
  "em_channel": 0,
  "devices": [
    {
      "name": "HeatPump",
      "ip": "192.168.86.103",
      "fetch_mode": "full"
    },
    {
      "name": "MainBuilding",
      "ip": "192.168.86.104",
      "fetch_mode": "full"
    },
    {
      "name": "Spa",
      "ip": "192.168.86.110",
      "fetch_mode": "hourly"
    }
  ]
}
```

### Kentät

| Kenttä | Pakollinen | Selitys |
|---|---|---|
| `name` | kyllä | Tiedostonimen osa (esim. `HeatPump` → `2026-09_lokaali_HeatPump_historia.csv`). Käytä vain kirjaimia/numeroita, ei välilyöntejä. |
| `ip` | kyllä | Laitteen paikallinen IP |
| `fetch_mode` | ei | `full` (oletus) tai `hourly` |
| `em_channel` | ei | Shellyn EM-kanava, yleensä `0` |

### `fetch_mode`

- **`full`** (suositus) — hakee dataa 6 tunnin pätkissä viimeisestä tallennetusta ajankohdasta. Kestää Connection reset -virheitä paremmin.
- **`hourly`** — hakee vain puuttuvat tunnit yksi kerrallaan. Hyödyllinen epävakaille laitteille.

> Älä commitoi `devices.json`-tiedostoa. Se on `.gitignore`-listalla juuri siksi, ettei kotiverkon IP-osoitteita joudu julkiseen repoon.

---

## 3. Aja datan haku

```bash
python3 scripts/paivita_shelly_data.py
```

**Mitä tapahtuu:**
1. Lataa `config/devices.json`
2. PING-tarkistus jokaiselle laitteelle (fail-fast)
3. Hakee uuden datan Shellyn `/emdata/<channel>/data.csv` -rajapinnasta
4. Tallentaa vain uudet rivit (inkrementaalinen — ei duplikaatteja)
5. Päivittää 15 minuutin koosteen

Ulkoisia Python-paketteja ei tarvita (vain standardikirjasto).

---

## 4. Missä data on?

```
data/
  2026-09_lokaali_HeatPump_historia.csv      # 1 min tarkkuus
  2026-09_15m_lokaali_HeatPump_historia.csv  # 15 min aggregointi
  2026-10_lokaali_HeatPump_historia.csv      # uusi kuukausi → uusi tiedosto
```

- Jako on **automaattisesti kuukausittainen** (`YYYY-MM_...`) rivin aikaleiman mukaan.
- Ensilataus hakee tyypillisesti ~7–10 vuorokautta (mitä laite vielä pitää muistissaan).
- Seuraavat ajot jatkavat viimeisestä timestampista → nopea päivitys.

### CSV-sarakkeet (20)

`timestamp`, vaihe A/B/C: energia, teho (avg/max/min), virta, jännite, sekä `n_avg_current`.

`a_avg_active_power` lasketaan energiasta (`Wh/min × 60 ≈ W`).

---

## 5. Automaattinen ajo (valinnainen)

macOS / Linux `cron` — esim. joka 15 min:

```bash
crontab -e
```

```cron
*/15 * * * * cd /polku/shelly-energy-data-collector && /usr/bin/python3 scripts/paivita_shelly_data.py >> /tmp/shelly-data.log 2>&1
```

Varmista, että cron-ajon kone on samassa verkossa kuin mittarit.

---

## 6. Vianmääritys

### PING epäonnistuu
```
❌ HeatPump (192.168.x.x) — EI VASTAA
```
1. Oletko samassa LAN/VLAN-verkossa tai VPN:ssä?
2. Onko IP vaihtunut? Tarkista reititin / Shelly UI.
3. Testaa: `ping <ip>` ja `curl "http://<ip>/emdata/0/data.csv?add_keys=true" | head`

### `config/devices.json` puuttuu
```
❌ Konfiguraatiota ei löydy: config/devices.json
```
→ `cp config/devices.example.json config/devices.json` ja täytä omat IP:t.

### Connection reset / hidas haku
Käytä `fetch_mode: "full"` (6 h pätkät) tai vaihda `"hourly"`. Älä hae koko historiaa yhdellä isolla CSV-kaadolla.

### Esimerkki-IP-varoitus
Skripti varoittaa, jos käytät vielä mallin osoitteita (`192.168.1.10` jne.). Vaihda ne omiin.

---

## Kansiorakenne

```
shelly-energy-data-collector/
├── README.md
├── LICENSE
├── .gitignore
├── config/
│   ├── devices.example.json   # mallipohja (repossa)
│   └── devices.json           # sinun IP:t (ei repossa)
├── data/                      # CSV-historia (ei repossa)
│   └── .gitkeep
└── scripts/
    ├── config_loader.py
    └── paivita_shelly_data.py
```

---

## Tietosuoja

- Repoon **ei** kuulu: `devices.json`, kerätty CSV-data, `.env`, salasanat.
- Shelly-haku on **paikallinen HTTP** kotiverkossa — ei API-avaimia.
- Julkaistessasi forkin pidä huoli, ettei `git status` näytä `devices.json` tai `data/*.csv`.

---

## Lisenssi

MIT — katso [LICENSE](LICENSE).
