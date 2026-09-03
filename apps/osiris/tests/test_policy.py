from osiris.policy import ActionClass, PolicyDenied, classify_text, decide, level_of, Level


def test_level0_sealed():
    for a in (
        ActionClass.LEAKED_DUMP,
        ActionClass.FOREIGN_ACCOUNT,
        ActionClass.BYPASS_PROTECTION,
        ActionClass.BIOMETRICS,
        ActionClass.PRIVATE_STALKING,
        ActionClass.CLOSED_PROFILE,
    ):
        assert level_of(a) is Level.L0
        try:
            decide(a, grant_ok=True)
            raise AssertionError("level 0 must not open even with grant_ok")
        except PolicyDenied:
            pass


def test_level1_needs_grant():
    try:
        decide(ActionClass.EXPORT_OUTBOUND, grant_ok=False)
        raise AssertionError("expected deny")
    except PolicyDenied:
        pass
    assert decide(ActionClass.EXPORT_OUTBOUND, grant_ok=True) is Level.L1


def test_level2_free():
    assert decide(ActionClass.PUBLIC_REGISTRY) is Level.L2


def test_classify_leaked():
    assert classify_text("купить combo list") is ActionClass.LEAKED_DUMP


def test_classify_captcha():
    assert classify_text("обход капчи") is ActionClass.BYPASS_PROTECTION
