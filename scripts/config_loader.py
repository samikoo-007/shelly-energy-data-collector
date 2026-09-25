#!/usr/bin/env python3
"""Lataa käyttäjän config/devices.json (kopio esimerkistä)."""
from __future__ import annotations

import json
import os
import sys

PROJEKTI_JUURI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_POLKU = os.path.join(PROJEKTI_JUURI, "config", "devices.json")
ESIMERKKI_POLKU = os.path.join(PROJEKTI_JUURI, "config", "devices.example.json")
DATA_HAKEMISTO = os.path.join(PROJEKTI_JUURI, "data")


def lataa_config() -> dict:
    if not os.path.exists(CONFIG_POLKU):
        print("❌ Konfiguraatiota ei löydy: config/devices.json")
        print("👉 Kopioi esimerkki ja täytä omat laitteesi:")
        print(f"   cp {ESIMERKKI_POLKU} {CONFIG_POLKU}")
        sys.exit(1)

    with open(CONFIG_POLKU, encoding="utf-8") as f:
        cfg = json.load(f)

    devices = cfg.get("devices") or []
    if not devices:
        print("❌ config/devices.json: 'devices' -lista on tyhjä.")
        sys.exit(1)

    for d in devices:
        if not d.get("name") or not d.get("ip"):
            print("❌ Jokaisella laitteella tarvitaan 'name' ja 'ip'.")
            sys.exit(1)
        if d.get("ip", "").startswith("192.168.1.") and d["ip"] in {
            "192.168.1.10",
            "192.168.1.11",
            "192.168.1.12",
        }:
            print(
                f"⚠️  Laite '{d['name']}' käyttää vielä esimerkki-IP:tä "
                f"({d['ip']}). Vaihda omaan osoitteeseen."
            )

    os.makedirs(DATA_HAKEMISTO, exist_ok=True)
    return cfg


def laitteet_dict(cfg: dict) -> dict[str, str]:
    """name → ip."""
    return {d["name"]: d["ip"] for d in cfg["devices"]}


def fetch_mode(cfg: dict, name: str) -> str:
    for d in cfg["devices"]:
        if d["name"] == name:
            return (d.get("fetch_mode") or "full").lower()
    return "full"
