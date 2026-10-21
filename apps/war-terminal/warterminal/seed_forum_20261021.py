"""Idempotent seed: companies from the owner's stand photos at the Future Military Forum 2026 (Prague, 21.10.2026).

No personal data: company-level facts only. The one e-mail is the address printed on the company's own flyer.
CPV codes are CPV 2008 (checked against the official table; 38113 is SONAR, radar apparatus is 38115). Where a stand
showed no product list the profile stays empty instead of guessed.

    python -m warterminal.seed_forum_20261021 [--data DIR]

DIR defaults to WAR_TERMINAL_DATA or ~/.war-terminal (the same war.db the app uses). Re-running never duplicates a company
and never overwrites a record the owner has edited since (key = fmf2026:<company>).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import contacts
from .store import Store

SOURCE = "Future Military Forum 2026, Praha, стенды (фото владельца)"
EVENT = "Future Military Forum 2026, Praha"
SLUG_PREFIX = "fmf2026"

# Slavic keywords end with * where the word changes its ending («радар*» = радар, радари, радарів ...).
COMPANIES: list[dict] = [
    {"company": "KV Defence Limited", "products": "",
     "notes": "визитка; продукты неизвестны — профиль CPV и ключевые слова не заполнены"},
    {"company": "Saab", "country": "SE",
     "products": "Carl-Gustaf M4 (ручное безоткатное орудие); бронеавтомобиль на стенде",
     "cpv_prefixes": ["35320000", "35331000", "35410000"],
     "keywords": {"en": ["carl-gustaf", "carl gustaf", "recoilless", "anti-tank", "armoured vehicle*", "armored vehicle*"],
                  "cs": ["bezzákluzov*", "protitankov*", "obrněn*"],
                  "uk": ["безвідкат*", "протитанков*", "броньован* автомоб*"],
                  "ru": ["безоткат*", "противотанков*", "бронеавтомоб*"]},
     "notes": "бронеавтомобиль был на стенде; модель не записана"},
    {"company": "MTS", "products": "Narwhal — крылатая ракета наземного базирования; турбореактивные двигатели 230–1600 N",
     "cpv_prefixes": ["35623000", "34731200", "35641100"],
     "keywords": {"en": ["cruise missile*", "turbojet*", "jet engine*"],
                  "cs": ["řízen* střel*", "proudov* motor*"],
                  "uk": ["крилат* ракет*", "турбореактивн*"],
                  "ru": ["крылат* ракет*", "турбореактивн*"]}},
    {"company": "Czechia Systems", "country": "CZ", "website": "www.czechiasystems.com",
     "public_email": "info@czechiasystems.com", "public_email_source": "напечатан на листовке компании на стенде",
     "products": "MURMUS — мобильный противодроновый комплекс (soft+hard kill, радар 12,5 км, дроны-перехватчики 350 км/ч)",
     "cpv_prefixes": ["35730000", "35720000", "35613000", "38115000"],
     "keywords": {"en": ["counter-drone", "counter-uas", "c-uas", "anti-drone", "drone detection", "jammer*", "interceptor drone*", "radar*"],
                  "cs": ["protidronov*", "antidronov*", "rušič*", "detekc* dron*", "radar*"],
                  "uk": ["протидрон*", "антидрон*", "реб", "радіоелектронн* боротьб*", "рлс", "радар*"],
                  "ru": ["противодрон*", "антидрон*", "рэб", "радиоэлектронн* борьб*", "рлс", "радар*"]}},
    {"company": "Fire Point", "country": "UA", "products": "FP-5 — крылатая ракета; FP-7 — баллистическая ракета; FP-1/FP-2 — БПЛА",
     "cpv_prefixes": ["35620000", "35613000", "34711200"],
     "keywords": {"en": ["cruise missile*", "ballistic missile*", "uav", "uas", "long-range drone*"],
                  "cs": ["řízen* střel*", "balistick* střel*", "bezpilot*"],
                  "uk": ["крилат* ракет*", "балістичн* ракет*", "бпла", "бпак", "безпілотн*", "ударн* дрон*"],
                  "ru": ["крылат* ракет*", "баллистическ* ракет*", "бпла", "беспилотн*", "ударн* дрон*"]}},
    {"company": "Origin", "products": "VTOL-дроны и дроны-перехватчики, дроновые платформы",
     "cpv_prefixes": ["35613000", "34711200"],
     "keywords": {"en": ["vtol", "interceptor drone*", "uav", "uas", "unmanned aerial", "drone*"],
                  "cs": ["dron*", "bezpilot*"],
                  "uk": ["дрон*", "бпла", "безпілотн*", "перехоплювач*"],
                  "ru": ["дрон*", "бпла", "беспилотн*", "перехватчик*"]}},
    {"company": "Česká zbrojovka (CZ)", "country": "CZ", "products": "CZ BREN 2 BR 7.62×51",
     "cpv_prefixes": ["35321000", "35341000"],
     "keywords": {"en": ["rifle*", "carbine*", "small arms", "7.62"],
                  "cs": ["pušk*", "pušek", "útočn* pušk*", "ručn* paln* zbra*"],
                  "uk": ["гвинтівк*", "стрілецьк* збро*", "автомат* гвинтівк*"],
                  "ru": ["винтовк*", "стрелков* оруж*"]}},
    {"company": "MKE", "country": "TR",
     "products": "винтовки MPT-76, MKE-300 BLK, KN-12, BORA-12; наземный робот (UGV) с дистанционным боевым модулем",
     "cpv_prefixes": ["35321000", "35400000"],
     "keywords": {"en": ["rifle*", "shotgun*", "ugv", "unmanned ground vehicle*", "remote weapon station*", "rws"],
                  "cs": ["pušk*", "pušek", "brokovnic*", "pozemn* robot*"],
                  "uk": ["гвинтівк*", "рушниц*", "наземн* робот*", "бойов* модул*"],
                  "ru": ["винтовк*", "ружь*", "наземн* робот*", "боев* модул*"]}},
    {"company": "VEX Hungary (Hungarian Defense and Space Export Agency)", "country": "HU",
     "products": "PGSR-3i «Beagle» — радар наземного наблюдения",
     "cpv_prefixes": ["35722000", "38115000"],
     "keywords": {"en": ["ground surveillance radar*", "surveillance radar*", "radar*"],
                  "cs": ["radar*", "radiolokátor*"],
                  "uk": ["радар*", "рлс", "радіолокац*"],
                  "ru": ["радар*", "рлс", "радиолокац*"]}},
    {"company": "ProLab Engineering",
     "products": "Orbitron 6000 — гибридно-электрический VTOL БПЛА (24 ч, 2000 км, MTOW 70 кг, полезная нагрузка 10 кг)",
     "cpv_prefixes": ["35613000", "34711200"],
     "keywords": {"en": ["vtol", "uav", "uas", "unmanned aerial", "long-endurance"],
                  "cs": ["dron*", "bezpilot*"],
                  "uk": ["бпла", "дрон*", "безпілотн*"],
                  "ru": ["бпла", "дрон*", "беспилотн*"]}},
    {"company": "Omnipol Defence + APS (Advanced Protection Systems)", "country": "CZ",
     "products": "FIELDctrl Range 3D — радар обнаружения дронов (FMCW 9,7–10 ГГц, микро-БПЛА до 9 км)",
     "cpv_prefixes": ["35722000", "38115000", "35125000"],
     "keywords": {"en": ["drone detection", "counter-uas", "c-uas", "counter-drone", "radar*"],
                  "cs": ["detekc* dron*", "protidronov*", "radar*"],
                  "uk": ["виявленн* дрон*", "протидрон*", "радар*", "рлс"],
                  "ru": ["обнаружени* дрон*", "антидрон*", "радар*", "рлс"]},
     "notes": "общий стенд Omnipol Defence и APS; страна — по Omnipol"},
    {"company": "North Vector Dynamics", "products": "пусковая установка CM-70",
     "cpv_prefixes": ["35322000", "35342000"],
     "keywords": {"en": ["launcher*", "rocket launcher*", "mlrs"],
                  "cs": ["raketomet*", "odpalovac*"],
                  "uk": ["пусков* установк*", "рсзв"],
                  "ru": ["пусков* установк*", "рсзо"]},
     "notes": "тип CM-70 по фото стенда не уточнён; CPV ориентировочные"},
    {"company": "Excalibur Army", "country": "CZ", "products": "бронетехника",
     "cpv_prefixes": ["35410000", "35420000"],
     "keywords": {"en": ["armoured vehicle*", "armored vehicle*", "apc", "infantry fighting vehicle*"],
                  "cs": ["obrněn*", "vojensk* vozid*"],
                  "uk": ["броньован*", "бронетехнік*", "бтр"],
                  "ru": ["бронетехник*", "бронированн*", "бтр"]}},
]


def records() -> list[tuple[str, dict]]:
    """(slug, validated record) for every company: the same validation as POST /contacts."""
    out = []
    for raw in COMPANIES:
        doc = contacts.clean({"event": EVENT, "source": SOURCE, "status": "new", **raw})
        out.append((contacts.slug(doc["company"], SLUG_PREFIX), doc))
    return out


def seed(store: Store) -> dict:
    created, existing = [], []
    for slug, doc in records():
        cid, new = store.add_contact(doc, slug=slug)
        (created if new else existing).append(cid)
    if created:                                                    # a re-run that adds nothing leaves no trace
        store.audit("seed-cli", "contacts.seed", {"seed": "seed_forum_20261021", "created": created, "existing": len(existing)})
    return {"created": len(created), "existing": len(existing), "total": len(created) + len(existing)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m warterminal.seed_forum_20261021", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=None, help="data dir (default WAR_TERMINAL_DATA or ~/.war-terminal)")
    args = ap.parse_args(argv)
    data_dir = Path(args.data or os.environ.get("WAR_TERMINAL_DATA") or Path.home() / ".war-terminal")
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "war.db")
    try:
        res = seed(store)
    finally:
        store.db.close()
    print(f"[seed_forum_20261021] {data_dir / 'war.db'}: created {res['created']}, already present {res['existing']}, total {res['total']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
