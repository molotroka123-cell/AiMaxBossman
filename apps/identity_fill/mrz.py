"""Passport MRZ (ICAO 9303, TD3: two lines of 44 characters): parsing and check digits. Pure, offline, no I/O."""
from __future__ import annotations

WEIGHTS = (7, 3, 1)


class MrzError(ValueError):
    pass


def _value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - 55
    if c == "<":
        return 0
    raise MrzError("character not allowed in an MRZ")


def check_digit(field: str) -> str:
    return str(sum(_value(c) * WEIGHTS[i % 3] for i, c in enumerate(field)) % 10)


def _date(yymmdd: str, future: bool) -> str:
    yy, mm, dd = int(yymmdd[:2]), yymmdd[2:4], yymmdd[4:6]
    century = 2000 if (future or yy <= 30) else 1900  # expiry dates are in this century; births after 2030 are not
    return f"{century + yy:04d}-{mm}-{dd}"


def parse_td3(line1: str, line2: str) -> dict:
    """Returns the identity fields. Raises MrzError when the shape or any check digit is wrong, so a misread never
    becomes data that gets typed into a form."""
    l1, l2 = line1.strip().upper().replace(" ", ""), line2.strip().upper().replace(" ", "")
    if len(l1) != 44 or len(l2) != 44 or l1[0] != "P":
        raise MrzError("not a TD3 passport MRZ (two lines of 44 characters starting with P)")
    number, birth, expiry, personal = l2[0:9], l2[13:19], l2[21:27], l2[28:42]
    personal_ok = check_digit(personal) == l2[42] or (personal.strip("<") == "" and l2[42] == "<")
    checks = {
        "document_number": check_digit(number) == l2[9],
        "birth_date": check_digit(birth) == l2[19],
        "expiry_date": check_digit(expiry) == l2[27],
        "personal_number": personal_ok,
        "composite": check_digit(l2[0:10] + l2[13:20] + l2[21:43]) == l2[43],
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise MrzError("check digit failed: " + ", ".join(failed))
    surname, _, given = l1[5:].partition("<<")
    return {
        "surname": surname.replace("<", " ").strip(),
        "given_names": given.replace("<", " ").strip(),
        "issuing_state": l1[2:5].replace("<", ""),
        "document_number": number.replace("<", ""),
        "nationality": l2[10:13].replace("<", ""),
        "birth_date": _date(birth, future=False),
        "sex": {"M": "M", "F": "F"}.get(l2[20], "X"),
        "expiry_date": _date(expiry, future=True),
    }


TO_LETTER = str.maketrans("0158", "OISB")
TO_DIGIT = str.maketrans("OQDISBZG", "00015826")


def repair_td3(line1: str, line2: str) -> tuple[str, str]:
    """Fixes the two mistakes OCR models make on an MRZ, using only the fixed layout of the zone:
    a wrong number of '<' fillers, and letter/digit look-alikes in positions where only one kind is allowed.
    The result is NOT trusted by itself: parse_td3() still has to pass every check digit."""
    l1 = "".join(c for c in line1.upper() if c.isalnum() or c == "<")
    l2 = "".join(c for c in line2.upper() if c.isalnum() or c == "<")
    l1 = l1.rstrip("<")[:44].ljust(44, "<")  # line 1 ends with fillers only
    if len(l2) != 44 and len(l2) > 30:
        # positions 0..27 are fixed-width fields; then a 14-character personal number padded with fillers, then the
        # two final check characters. A miscounted filler run is the only thing that can change the length here.
        head, personal, last2 = l2[:28], l2[28:-2].rstrip("<"), l2[-2:]
        if len(personal) <= 14:
            l2 = head + personal.ljust(14, "<") + last2
    if len(l2) == 44:
        chars = list(l2)
        for i in (10, 11, 12):  # nationality: letters
            chars[i] = chars[i].translate(TO_LETTER)
        for i in (9, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 43):  # dates and check digits: digits
            chars[i] = chars[i].translate(TO_DIGIT)
        l2 = "".join(chars)
    return l1, l2
