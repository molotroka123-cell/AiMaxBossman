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
    {"company": "Origin", "products": "VTOL-дроны и дроны-перехватчики, дроновые платформы, FPV-комплексы и аккумуляторы БПЛА",
     "cpv_prefixes": ["35613000", "34711200", "31400000"],
     "keywords": {"en": ["vtol", "interceptor drone*", "uav", "uas", "unmanned aerial", "drone*", "fpv"],
                  "cs": ["dron*", "bezpilot*", "fpv", "baterie*"],
                  "uk": ["дрон*", "бпла", "безпілотн*", "перехоплювач*", "fpv", "окуляри fpv", "акумулятор*", "matrice", "tulip tech"],
                  "ru": ["дрон*", "бпла", "беспилотн*", "перехватчик*", "fpv", "аккумулятор*"]}},
    {"company": "Česká zbrojovka (CZ)", "country": "CZ", "products": "CZ BREN 2 BR 7.62×51, стрелковое оружие, ЗИП, оружейные ремни, мишенные и тренажёрные комплексы",
     "cpv_prefixes": ["35321000", "35341000", "35340000", "35740000"],
     "keywords": {"en": ["rifle*", "carbine*", "small arms", "7.62", "weapon sling*", "weapon part*", "shooting simulator*"],
                  "cs": ["pušk*", "pušek", "útočn* pušk*", "ručn* paln* zbra*", "popruh* na zbra*", "příslušenstv* k zbra*", "náhradn* díl*"],
                  "uk": ["гвинтівк*", "стрілецьк* збро*", "автомат* гвинтівк*", "очищенн* збро*", "мішен*", "симулятор* бойов*", "тренажерн*", "страйкбольн*"],
                  "ru": ["винтовк*", "стрелков* оруж*", "чистк* оруж*", "мишен*", "тренажер*"]}},
    {"company": "MKE", "country": "TR",
     "products": "винтовки MPT-76, MKE-300 BLK, KN-12, BORA-12; наземный робот (UGV) с дистанционным боевым модулем",
     "cpv_prefixes": ["35321000", "35400000"],
     "keywords": {"en": ["rifle*", "shotgun*", "ugv", "unmanned ground vehicle*", "remote weapon station*", "rws", "robotics"],
                  "cs": ["pušk*", "pušek", "brokovnic*", "pozemn* robot*", "robotik*"],
                  "uk": ["гвинтівк*", "рушниц*", "наземн* робот*", "бойов* модул*", "робототехнік*"],
                  "ru": ["винтовк*", "ружь*", "наземн* робот*", "боев* модул*", "робототехник*"]}},
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
     "products": "FIELDctrl Range 3D — радар обнаружения дронов (FMCW 9,7–10 ГГц, микро-БПЛА до 9 км), тактическая радиосвязь и аксессуары",
     "cpv_prefixes": ["35722000", "38115000", "35125000", "32230000", "32236000"],
     "keywords": {"en": ["drone detection", "counter-uas", "c-uas", "counter-drone", "radar*", "handheld radio*", "tactical radio*"],
                  "cs": ["detekc* dron*", "protidronov*", "radar*", "radiostanic*", "komunikačn* prostředk*"],
                  "uk": ["виявленн* дрон*", "протидрон*", "радар*", "рлс", "радіостанц*"],
                  "ru": ["обнаружени* дрон*", "антидрон*", "радар*", "рлс", "радиостанц*"]},
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

# Batch 2 (forum-16..20.jpg, IMG_1390.mov, forum-03.jpg background stands):
EXTRA_COMPANIES: list[dict] = [
    {"company": "Glock Ges.m.b.H.", "country": "AT",
     "products": "G49 Gen6 COA (пистолет 9×19 с коллиматорным прицелом Aimpoint COA и адаптерными пластинами)",
     "cpv_prefixes": ["35321100", "35321000"],
     "keywords": {"en": ["pistol*", "handgun*", "small arms", "9x19"],
                  "cs": ["pistol*", "krátk* kulov* zbra*", "ručn* paln* zbra*"],
                  "uk": ["пістолет*", "стрілецьк* збро*"],
                  "ru": ["пистолет*", "стрелков* оруж*"]},
     "notes": "фото стенда forum-16.jpg (G49 Gen6 COA)"},
    {"company": "Meprolight", "country": "IL",
     "products": "коллиматорные, тепловизионные и ночные прицелы (Mepro M5, MOR, Foresight, Tru-Vision) для стрелкового оружия и пулемётов",
     "cpv_prefixes": ["38636000", "38632000", "35221000", "35341000"],
     "keywords": {"en": ["sight*", "red dot", "thermal*", "night vision", "electro-optical", "optic*"],
                  "cs": ["zaměřovač*", "kolimátor*", "termoviz*", "nočn* viděn*", "optik*"],
                  "uk": ["приціл*", "коліматор*", "тепловізор*", "тепловізійн*", "нічн* баченн*", "оптичн*"],
                  "ru": ["прицел*", "коллиматор*", "тепловизор*", "ночн* видени*", "оптик*"]},
     "notes": "фото стенда forum-17.jpg и forum-18.jpg (Everything You Aim For)"},
    {"company": "GLOMEX Military Supplies", "country": "CZ", "website": "www.glomex-ms.com",
     "products": "лётные шлемы и кислородные маски (Air Forces), баллистические шлемы и жилеты, активные стрелковые наушники, тактическая форма/обувь/обогрев (Special Forces), крепления ПНВ",
     "cpv_prefixes": ["35812000", "35810000", "18444000", "35113400", "18100000", "18200000", "18800000"],
     "keywords": {"en": ["helmet*", "ballistic helmet*", "flight helmet*", "protective gear", "body armour", "body armor", "hearing protection", "tactical boots"],
                  "cs": ["přilb*", "balistick* přilb*", "leteck* přilb*", "výstroj*", "kukla", "vesta balistick*", "střeleck* sluchátk*", "chránič* sluch*", "zimn* bund*", "tričk*", "zásahov* oblek*", "obuv zásahov*"],
                  "uk": ["шолом*", "протикулев* шолом*", "балістичн* шолом*", "екіпіруванн*", "бронежилет*", "індивідуальн* обігрів*", "активн* навушник*"],
                  "ru": ["шлем*", "баллистическ* шлем*", "защитн* экипировк*", "бронежилет*", "стрелков* наушник*", "индивидуальн* обогрев*"]},
     "notes": "фото стенда forum-19.jpg (GLOMEX Military Supplies — Air Forces / Special Forces)"},
    {"company": "EVPÚ Defence", "country": "CZ", "website": "www.evpudefence.com",
     "products": "DSS — оптико-электронная мультисенсорная турель (дневная/тепловизионная камера, лазерный дальномер), носимые/бортовые камеры и дистанционно управляемые боевые модули",
     "cpv_prefixes": ["35720000", "38636000", "35125000", "35420000"],
     "keywords": {"en": ["electro-optical", "gimbal*", "thermal camera*", "surveillance system*", "remote weapon station*", "body camera*"],
                  "cs": ["elektrooptick*", "termokamer*", "pozorovac* systém*", "věž*", "body kamer*"],
                  "uk": ["оптико-електронн*", "тепловізор*", "гіростабілізован*", "спостереженн*", "бодікамер*"],
                  "ru": ["оптико-электронн*", "тепловизионн*", "гиростабилизированн*", "наблюдени*"]},
     "notes": "видео стенда IMG_1390.mov (турель DSS на треноге)"},
    {"company": "Sellier & Bellot", "country": "CZ", "website": "www.sellier-bellot.cz",
     "products": "Sniper Line Ammunition — снайперские и стрелковые боеприпасы (5.56×45, 7.62×51, 9×19, .338 Lapua Magnum)",
     "cpv_prefixes": ["35331000", "35331500", "35330000"],
     "keywords": {"en": ["ammunition", "cartridge*", "small arms ammunition", "5.56", "7.62", "9x19", "sniper"],
                  "cs": ["náboj*", "střeliv*", "munic*", "malorážkov*"],
                  "uk": ["набо*", "патрон*", "боєприпас*"],
                  "ru": ["патрон*", "боеприпас*"]},
     "notes": "видео стенда IMG_1390.mov (стенд Sellier & Bellot Sniper Line Ammunition)"},
    {"company": "Yantra India Limited", "country": "IN",
     "products": "артиллерийские гильзы, корпуса снарядов, тара для боеприпасов и металлургические комплектующие (A Govt. of India Enterprise)",
     "cpv_prefixes": ["35330000", "35331000", "35340000"],
     "keywords": {"en": ["ammunition", "artillery shell*", "ordnance", "mortar*"],
                  "cs": ["munic*", "dělostřeleck*", "střeliv*"],
                  "uk": ["боєприпас*", "артилерійськ*", "снаряд*"],
                  "ru": ["боеприпас*", "артиллерийск*", "снаряд*"]},
     "notes": "фото стенда forum-17.jpg и forum-18.jpg (стенд на заднем плане)"},
    {"company": "ERA a.s.", "country": "CZ", "website": "www.era.aero",
     "products": "VERA-NG — пассивная система радиотехнической разведки и контроля воздушного пространства (Passive ESM Tracker)",
     "cpv_prefixes": ["35720000", "35730000", "38115000"],
     "keywords": {"en": ["passive radar*", "surveillance*", "air defence", "electronic warfare", "radar*"],
                  "cs": ["pasivn* radiolokátor*", "průzkum*", "radar*", "protivzdušn*"],
                  "uk": ["пасивн* радар*", "радіотехнічн* розвідк*", "рлс", "реб"],
                  "ru": ["пассивн* радар*", "радиотехническ* разведк*", "рлс", "рэб"]},
     "notes": "фото зала forum-03.jpg (стенд ERA)"},
    {"company": "Aero Vodochody Aerospace", "country": "CZ", "website": "www.aero.cz",
     "products": "учебно-боевой самолёт L-39NG Skyfox, авиационные агрегаты и ТОиР авиатехники",
     "cpv_prefixes": ["35611000", "34711100", "34730000", "50210000"],
     "keywords": {"en": ["aircraft*", "aviation", "trainer aircraft*", "aerospace"],
                  "cs": ["letadl*", "leteck*", "letoun*"],
                  "uk": ["літак*", "авіаційн*"],
                  "ru": ["самолет*", "авиационн*"]},
     "notes": "фото зала forum-03.jpg (стенд AERO)"},
]

ALL_COMPANIES: list[dict] = COMPANIES + EXTRA_COMPANIES


def records(*, include_extra: bool = False) -> list[tuple[str, dict]]:
    """(slug, validated record) for every company: the same validation as POST /contacts."""
    out = []
    source_list = ALL_COMPANIES if include_extra else COMPANIES
    for raw in source_list:
        doc = contacts.clean({"event": EVENT, "source": SOURCE, "status": "new", **raw})
        out.append((contacts.slug(doc["company"], SLUG_PREFIX), doc))
    return out


def seed(store: Store, *, include_extra: bool = False, update_existing: bool = False) -> dict:
    created, existing, updated = [], [], []
    for slug, doc in records(include_extra=include_extra):
        cid, new = store.add_contact(doc, slug=slug)
        if new:
            created.append(cid)
        else:
            existing.append(cid)
            if update_existing:
                cur_doc = store.get_contact(cid) or {}
                merged = {k: v for k, v in cur_doc.items() if k in contacts.FIELDS}
                merged.update(doc)
                store.update_contact(cid, merged)
                updated.append(cid)
    if created or updated:                                         # a re-run that adds nothing leaves no trace
        store.audit("seed-cli", "contacts.seed", {
            "seed": "seed_forum_20261021",
            "created": created,
            "updated": len(updated),
            "existing": len(existing),
        })
    res = {
        "created": len(created),
        "existing": len(existing),
        "total": len(created) + len(existing),
    }
    if update_existing:
        res["updated"] = len(updated)
    return res


def seed_all(store: Store, *, update_existing: bool = False) -> dict:
    """Seed both original 13 forum stands and the 8 additional forum stands (21 total)."""
    return seed(store, include_extra=True, update_existing=update_existing)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m warterminal.seed_forum_20261021", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=None, help="data dir (default WAR_TERMINAL_DATA or ~/.war-terminal)")
    ap.add_argument("--include-extra", action="store_true", help="also seed batch-2 forum companies (21 total)")
    ap.add_argument("--update-existing", action="store_true", help="update products/CPV/keywords on existing fmf2026 records")
    args = ap.parse_args(argv)
    data_dir = Path(args.data or os.environ.get("WAR_TERMINAL_DATA") or Path.home() / ".war-terminal")
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "war.db")
    try:
        res = seed(store, include_extra=bool(args.include_extra), update_existing=bool(args.update_existing))
    finally:
        store.db.close()
    upd_part = f", updated {res.get('updated', 0)}" if args.update_existing else ""
    print(f"[seed_forum_20261021] {data_dir / 'war.db'}: created {res['created']}{upd_part}, already present {res['existing']}, total {res['total']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

