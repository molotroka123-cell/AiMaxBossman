"""All data here is the ICAO 9303 specimen passport (UTOPIA, ANNA MARIA ERIKSSON). No real person."""
import pytest
from apps.identity_fill import mrz, vault

L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
L2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def specimen() -> vault.Vault:
    rec = mrz.parse_td3(L1, L2)
    rec.update(email="anna.eriksson@example.com", city="Utopia City")
    return vault.Vault(rec)


def test_icao_specimen_parses():
    r = mrz.parse_td3(L1, L2)
    assert r == {"surname": "ERIKSSON", "given_names": "ANNA MARIA", "issuing_state": "UTO", "document_number": "L898902C3",
                 "nationality": "UTO", "birth_date": "1974-08-12", "sex": "F", "expiry_date": "2012-04-15"}


@pytest.mark.parametrize("pos", [0, 5, 14, 22, 30])
def test_one_misread_character_is_rejected(pos):
    wrong = "9" if L2[pos] != "9" else "8"
    with pytest.raises(mrz.MrzError):
        mrz.parse_td3(L1, L2[:pos] + wrong + L2[pos + 1:])


def test_wrong_shape_is_rejected():
    with pytest.raises(mrz.MrzError):
        mrz.parse_td3(L1[:-1], L2)
    with pytest.raises(mrz.MrzError):
        mrz.parse_td3("I" + L1[1:], L2)


def test_vault_never_shows_values():
    v = specimen()
    text = repr(v) + str(v) + str(v.fields()) + str(v.describe())
    for secret in ("ERIKSSON", "L898902C3", "1974-08-12", "anna.eriksson@example.com"):
        assert secret not in text
    assert v.describe()["surname"] == {"present": True, "length": 8}


def test_resolve_and_date_parts():
    v = specimen()
    assert v.resolve("{{id.given_names}} {{id.surname}}") == "ANNA MARIA ERIKSSON"
    assert v.resolve("{{id.birth_date:day}}.{{id.birth_date:month}}.{{id.birth_date:year}}") == "12.08.1974"


def test_unknown_field_is_refused():
    v = specimen()
    with pytest.raises(vault.VaultError):
        v.resolve("{{id.phone}}")
    with pytest.raises(vault.VaultError):
        vault.Vault({"mother_maiden_name": "x"})


def test_redact_removes_every_value():
    v = specimen()
    seen = "Welcome, anna maria ERIKSSON! Passport L898902C3, born 1974-08-12, mail anna.eriksson@example.com"
    red = v.redact(seen)
    for secret in ("ERIKSSON", "anna maria", "L898902C3", "1974-08-12", "example.com"):
        assert secret.lower() not in red.lower()
    assert "{{id.surname}}" in red and "{{id.document_number}}" in red


def test_run_plan_types_real_values_but_audits_placeholders():
    v = specimen()
    typed = []
    plan = [{"field": "First name", "value": "{{id.given_names}}"}, {"field": "Last name", "value": "{{id.surname}}"},
            {"field": "Birth year", "value": "{{id.birth_date:year}}"}]
    audit = vault.run_plan(v, plan, lambda label, value: typed.append((label, value)))
    assert typed == [("First name", "ANNA MARIA"), ("Last name", "ERIKSSON"), ("Birth year", "1974")]
    dumped = str(audit)
    assert "ERIKSSON" not in dumped and "1974" not in dumped and "{{id.surname}}" in dumped


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://192.168.1.20:11434", "http://example.com"])
def test_remote_ocr_endpoint_is_an_error(url):
    with pytest.raises(vault.VaultError):
        vault.assert_local(url)


def test_loopback_ocr_endpoint_is_allowed():
    assert vault.assert_local("http://127.0.0.1:11434") and vault.assert_local("http://localhost:11434")


def test_repair_fixes_the_misreads_seen_from_the_local_ocr_model():
    # exactly what longwayxu/xiaomi-ocr-0 returned for the specimen on 10.11.2026: extra fillers and O read as 0
    bad1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<<<<"
    bad2 = "L898902C36UT07408122F1204159ZE184226B<<<<<<10"
    assert mrz.repair_td3(bad1, bad2) == (L1, L2)
    assert mrz.parse_td3(*mrz.repair_td3(bad1, bad2))["document_number"] == "L898902C3"


def test_repair_does_not_rescue_a_wrong_digit():
    with pytest.raises(mrz.MrzError):
        mrz.parse_td3(*mrz.repair_td3(L1, L2[:3] + "7" + L2[4:]))


def test_repair_leaves_a_correct_mrz_unchanged():
    assert mrz.repair_td3(L1, L2) == (L1, L2)


def test_signup_templates_use_only_placeholders_and_known_fields():
    import json, pathlib
    folder = pathlib.Path(__file__).parent.parent / "templates"
    files = sorted(folder.glob("*.json"))
    assert {f.stem for f in files} == {"instagram", "reddit", "telegram", "tiktok", "x"}
    for f in files:
        tpl = json.loads(f.read_text(encoding="utf-8"))
        assert tpl["url"].startswith("https://") and "captcha" in tpl["owner_steps"]
        for step in tpl["steps"]:
            names = vault.PLACEHOLDER.findall(step["value"])
            assert names, f"{f.name}: a step without a placeholder would carry a literal value"
            assert all(n in vault.FIELDS for n, _ in names)
            assert vault.PLACEHOLDER.sub("", step["value"]).strip() == ""  # nothing but placeholders and spaces


def test_find_identity_returns_a_list_without_raising():
    assert isinstance(vault.find_identity(), list)


def test_sealed_drive_roundtrip_and_wrong_passphrase():
    import json
    from apps.identity_fill import drive
    rec = mrz.parse_td3(L1, L2)
    blob = drive.seal(rec, "correct horse battery")
    dumped = json.dumps(blob)
    for secret in ("ERIKSSON", "L898902C3", "1974"):
        assert secret not in dumped
    assert drive.unseal(blob, "correct horse battery").resolve("{{id.surname}}") == "ERIKSSON"
    with pytest.raises(vault.VaultError):
        drive.unseal(blob, "wrong passphrase!!")
    tampered = dict(blob, ct=blob["ct"][:-4] + "AAAA")
    with pytest.raises(vault.VaultError):
        drive.unseal(tampered, "correct horse battery")


def test_short_passphrase_and_unknown_field_refused():
    from apps.identity_fill import drive
    with pytest.raises(vault.VaultError):
        drive.seal({"surname": "X"}, "short")
    with pytest.raises(vault.VaultError):
        drive.seal({"password": "x"}, "long enough passphrase")
