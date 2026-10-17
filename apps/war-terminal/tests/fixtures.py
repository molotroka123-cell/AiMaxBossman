"""Synthetic API payloads shaped like the real TED / USAspending / GDELT answers (real field names, invented notices)."""
import copy


def ted_notice(pub, title, *, buyer="Mesto Brno", country="CZE", cpv=("09134200",), value=("250000",), cur=("CZK",), deadline="2026-11-20+01:00",
               ntype="cn-standard"):
    return {"publication-number": pub, "notice-title": {"eng": title, "ces": title + " (cs)"}, "buyer-name": {"ces": [buyer]}, "buyer-country": [country],
            "publication-date": "2026-10-10+02:00", "deadline-receipt-tender-date-lot": [deadline] if deadline else None,
            "estimated-value-lot": list(value) if value else None, "estimated-value-cur-lot": list(cur) if cur else None,
            "classification-cpv": list(cpv), "place-of-performance-country-lot": [country], "notice-type": ntype}


def ted_payload():
    return {"totalNoticeCount": 6, "notices": [
        ted_notice("100001-2026", "Diesel supply for municipal fleet"),                                                     # good civil fuel
        ted_notice("100002-2026", "Food parcels", cpv=("15800000",), value=("52000",), cur=("EUR",), buyer="Social services"),
        ted_notice("100003-2026", "Cancelled-like award notice", ntype="can-standard", cpv=("60100000",)),                   # already awarded
        ted_notice("100004-2026", "Fuel for buyer in restricted country", buyer="Ministry X", country="RUS"),
        ted_notice("100005-2026", "Ammunition 7.62", cpv=("35330000",), value=("90000",), cur=("EUR",), buyer="Defence agency", country="LTU"),
        ted_notice("100006-2026", "Transport services, value without currency", cpv=("60100000",), value=("40000",), cur=None),
    ]}


def usa_payload():
    return {"results": [{"Award ID": "W1", "Recipient Name": "ACME FUEL LLC", "Award Amount": 1250000.5, "Awarding Agency": "DHS",
                         "Description": "FUEL", "generated_internal_id": "CONT_W1"}]}


def gdelt_payload():
    return {"articles": [{"title": "Fuel logistics disrupted", "url": "https://example.org/a", "domain": "example.org", "seendate": "20261016T120000Z",
                          "sourcecountry": "Guinea", "language": "English"}]}


def fake_post(ted=None, usa=None):
    ted = ted if ted is not None else ted_payload()
    usa = usa if usa is not None else usa_payload()

    def post(url, body, **kw):
        if "ted.europa.eu" in url:
            return copy.deepcopy(ted)
        if "usaspending" in url:
            return copy.deepcopy(usa)
        raise AssertionError(f"unexpected POST {url}")
    return post


def fake_get(url, params=None, **kw):
    if "gdeltproject" in url:
        return gdelt_payload()
    raise AssertionError(f"unexpected GET {url}")
