import os
import pytest
from bossman.trading_learning.chart_ocr import (
    parse_price, parse_ohlc, parse_title, read_frame, ocr_block
)


def test_parse_price():
    assert parse_price("118,424.09") == 118424.09
    assert parse_price("118.424.09") == 118424.09
    assert parse_price("118,424") == 118424.0
    assert parse_price("117.020") == 117020.0
    assert parse_price("6,274.25") == 6274.25
    assert parse_price("117,376.9") == 117376.9
    assert parse_price("116,318.40") == 116318.4
    assert parse_price("119,150.80") == 119150.8
    assert parse_price("116,695.36") == 116695.36
    assert parse_price("116,318.40178386296.30117,28") is None
    assert parse_price("119,150.80 H119,179.13L119,025.05C119.086.51-65.04（-0.05%%)") is None
    assert parse_price("") is None
    assert parse_price("abc") is None
    assert parse_price("118,42") is None  # Not exactly 3 digits after first sep
    assert parse_price("1184,424") is None  # 4 digits before sep


def test_parse_ohlc():
    assert parse_ohlc("O118.650.69H118.672.59L118.441.46C118.597.44") == {
        'open': 118650.69, 'high': 118672.59, 'low': 118441.46, 'close': 118597.44
    }
    assert parse_ohlc("0118,708.23H118,828.30L118,240.72C118,424.09") == {
        'open': 118708.23, 'high': 118828.30, 'low': 118240.72, 'close': 118424.09
    }
    assert parse_ohlc("0119,455 H120,275L119,360 C119,875") == {
        'open': 119455.0, 'high': 120275.0, 'low': 119360.0, 'close': 119875.0
    }
    assert parse_ohlc("0117,084H117,376.9L117,000.1C117.020MV62M") == {
        'open': 117084.0, 'high': 117376.9, 'low': 117000.1, 'close': 117020.0
    }
    assert parse_ohlc("0116,695.36 H117,100.00 L116,615.71 C117,067.24") == {
        'open': 116695.36, 'high': 117100.00, 'low': 116615.71, 'close': 117067.24
    }
    assert parse_ohlc("06,274.25H6,275.25L6,249.25C6,250.50") == {
        'open': 6274.25, 'high': 6275.25, 'low': 6249.25, 'close': 6250.50
    }
    assert parse_ohlc("O119,150.80 H119,179.13L119,025.05C119.086.51-65.04（-0.05%%)") == {
        'open': 119150.80, 'high': 119179.13, 'low': 119025.05, 'close': 119086.51
    }
    # Sanity check fail: high < open
    assert parse_ohlc("O100,000H99,000L98,000C99,500") is None
    # Garbage
    assert parse_ohlc("0116,318.40178386296.30117,28") is None
    # Missing parts
    assert parse_ohlc("O118.650.69H118.672.59") is None
    assert parse_ohlc("") is None


def test_parse_title():
    assert parse_title("BTCUSD.3O-COINBASE") == {
        'symbol': 'BTCUSD', 'timeframe': '30m', 'exchange': 'COINBASE'
    }
    assert parse_title("BTC1!·30.CME") == {
        'symbol': 'BTC1!', 'timeframe': '30m', 'exchange': 'CME'
    }
    assert parse_title("BTCUSD.1h-COINBASE") == {
        'symbol': 'BTCUSD', 'timeframe': '1h', 'exchange': 'COINBASE'
    }
    assert parse_title("ES1!-30-CME") == {
        'symbol': 'ES1!', 'timeframe': '30m', 'exchange': 'CME'
    }
    assert parse_title("Bybit:BTCUSD") == {
        'symbol': 'BTCUSD', 'timeframe': None, 'exchange': 'Bybit'
    }
    assert parse_title("Indicators88") is None
    assert parse_title("5m15m30m") is None
    assert parse_title("BTCUSD-60-COINBASE") == {
        'symbol': 'BTCUSD', 'timeframe': '1h', 'exchange': 'COINBASE'
    }
    assert parse_title("BTCUSD-D-COINBASE") == {
        'symbol': 'BTCUSD', 'timeframe': '1d', 'exchange': 'COINBASE'
    }
    assert parse_title("BTCUSD 4h COINBASE") == {
        'symbol': 'BTCUSD', 'timeframe': '4h', 'exchange': 'COINBASE'
    }
    assert parse_title("") is None


def test_ocr_block():
    info = {
        'symbol': 'BTCUSD',
        'exchange': 'COINBASE',
        'timeframe': '30m',
        'open': 118650.69,
        'high': 118672.59,
        'low': 118441.46,
        'close': 118597.44,
        'raw': []
    }
    result = ocr_block(info)
    assert "C=118597.44" in result
    assert "last_close = C" in result
    assert result.startswith("OCR шапки графика: symbol=BTCUSD")
    # Check exact format
    expected = "OCR шапки графика: symbol=BTCUSD exchange=COINBASE timeframe=30m O=118650.69 H=118672.59 L=118441.46 C=118597.44 (last_close = C)"
    assert result == expected


@pytest.fixture
def exam_dir():
    exam_dir = os.getenv('K1M6A_EXAM_DIR', 'C:\\Users\\asd\\Bossman\\k1m6a-exam-20261008')
    if not os.path.exists(exam_dir):
        pytest.skip("Exam directory not found: " + exam_dir)
    return exam_dir


@pytest.fixture
def ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        pytest.skip("rapidocr_onnxruntime not available")
    return RapidOCR()


def test_integration_hdr_0(exam_dir, ocr_engine):
    """Test on mosaic image hdr_0.png - full image OCR without crop"""
    path = os.path.join(exam_dir, 'ref', 'hdr_0.png')
    if not os.path.exists(path):
        pytest.skip(f"File not found: {path}")

    result, _ = ocr_engine(path)
    if not result:
        pytest.fail("OCR returned nothing on a real frame that exists - the reader is broken, not absent")

    closes = []
    for item in result:
        if len(item) < 2:
            continue
        text = item[1]
        ohlc = parse_ohlc(text)
        if ohlc:
            closes.append(ohlc['close'])

    assert 118597.44 in closes, f"Expected 118597.44 in closes, got {closes}"
    assert 118424.09 in closes, f"Expected 118424.09 in closes, got {closes}"


def test_integration_hdr_1(exam_dir, ocr_engine):
    """Test on mosaic image hdr_1.png"""
    path = os.path.join(exam_dir, 'ref', 'hdr_1.png')
    if not os.path.exists(path):
        pytest.skip(f"File not found: {path}")

    result, _ = ocr_engine(path)
    if not result:
        pytest.fail("OCR returned nothing on a real frame that exists - the reader is broken, not absent")

    closes = []
    for item in result:
        if len(item) < 2:
            continue
        text = item[1]
        ohlc = parse_ohlc(text)
        if ohlc:
            closes.append(ohlc['close'])

    assert 6250.5 in closes, f"Expected 6250.5 in closes, got {closes}"


def test_integration_hdr_2(exam_dir, ocr_engine):
    """Test on mosaic image hdr_2.png - check close and title"""
    path = os.path.join(exam_dir, 'ref', 'hdr_2.png')
    if not os.path.exists(path):
        pytest.skip(f"File not found: {path}")

    result, _ = ocr_engine(path)
    if not result:
        pytest.fail("OCR returned nothing on a real frame that exists - the reader is broken, not absent")

    closes = []
    titles = []
    for item in result:
        if len(item) < 2:
            continue
        text = item[1]
        ohlc = parse_ohlc(text)
        if ohlc:
            closes.append(ohlc['close'])
        title = parse_title(text)
        if title:
            titles.append(title)

    assert 117605.0 in closes, f"Expected 117605.0 in closes, got {closes}"
    assert any(
        t['symbol'] == 'BTC1!' and t['timeframe'] == '30m' and t['exchange'] == 'CME'
        for t in titles
    ), f"Expected BTC1! 30m CME in titles, got {titles}"


def test_integration_hdr_3(exam_dir, ocr_engine):
    """Test on mosaic image hdr_3.png"""
    path = os.path.join(exam_dir, 'ref', 'hdr_3.png')
    if not os.path.exists(path):
        pytest.skip(f"File not found: {path}")

    result, _ = ocr_engine(path)
    if not result:
        pytest.fail("OCR returned nothing on a real frame that exists - the reader is broken, not absent")

    closes = []
    for item in result:
        if len(item) < 2:
            continue
        text = item[1]
        ohlc = parse_ohlc(text)
        if ohlc:
            closes.append(ohlc['close'])

    assert 117067.24 in closes, f"Expected 117067.24 in closes, got {closes}"
    assert 117067.77 in closes, f"Expected 117067.77 in closes, got {closes}"


def test_integration_read_frame_real(exam_dir, ocr_engine):
    """
    Test read_frame on real 1280x720 frame.
    Path: <K1M6A_EXAM_DIR>/../../AppData/Local/Bossman/CommandCenter/learning/k1m6a-youtube/raw/L5cuSgFBcko/frames/smart_004_00306.jpg
    Or via K1M6A_FRAMES_DIR env.
    Expected: BTCUSD, COINBASE, 30m, close=118424.09, low=118240.72
    """
    # Try env var first
    frames_dir = os.getenv('K1M6A_FRAMES_DIR')
    if frames_dir:
        frame_path = os.path.join(frames_dir, 'L5cuSgFBcko', 'frames', 'smart_004_00306.jpg')
    else:
        # Construct from exam_dir: ../../AppData/...
        frame_path = os.path.normpath(os.path.join(
            exam_dir, '..', '..', 'AppData', 'Local', 'Bossman', 'CommandCenter',
            'learning', 'k1m6a-youtube', 'raw', 'L5cuSgFBcko', 'frames', 'smart_004_00306.jpg'
        ))

    if not os.path.exists(frame_path):
        pytest.skip(f"Frame file not found: {frame_path}")

    info = read_frame(frame_path, ocr=ocr_engine)

    assert info['symbol'] == 'BTCUSD', f"Expected BTCUSD, got {info['symbol']}"
    assert info['exchange'] == 'COINBASE', f"Expected COINBASE, got {info['exchange']}"
    assert info['timeframe'] == '30m', f"Expected 30m, got {info['timeframe']}"
    assert info['close'] == 118424.09, f"Expected 118424.09, got {info['close']}"
    assert info['low'] == 118240.72, f"Expected 118240.72, got {info['low']}"
