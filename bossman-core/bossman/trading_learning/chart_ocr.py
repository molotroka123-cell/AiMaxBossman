import re
from typing import Optional, Dict, List, Union, Any


def parse_price(token: str) -> Optional[float]:
    """
    Parse a price string from OCR output.
    Handles both comma and dot as thousand/decimal separators.
    Pattern: 1-3 digits, separator, exactly 3 digits, optional separator and 1-2 digits.
    First separator is thousands, second is decimal point.
    """
    if not token:
        return None
    # Pattern: 1-3 digits, [.,], 3 digits, optional [.,] and 1-2 digits
    pattern = r'^(\d{1,3})([.,])(\d{3})(?:([.,])(\d{1,2}))?$'
    match = re.fullmatch(pattern, token)
    if not match:
        return None

    int_part = match.group(1) + match.group(3)
    if match.group(4) and match.group(5):
        # Has decimal part
        dec_part = match.group(5)
        num_str = f"{int_part}.{dec_part}"
    else:
        num_str = int_part

    try:
        return float(num_str)
    except ValueError:
        return None


def parse_ohlc(text: str) -> Optional[Dict[str, float]]:
    """
    Parse OHLC values from OCR text.
    Looks for O/H/L/C pattern (O can be O, 0, or U).
    Validates sanity: low <= min(open, close) and max(open, close) <= high.
    """
    if not text:
        return None

    # Price pattern: digits, dots, commas (validated by parse_price)
    price_pattern = r'[\d.,]+'

    # Match O/0/U at start or after whitespace, then price, then H, L, C with optional spaces
    pattern = rf'(?:^|\s)([O0U])\s*({price_pattern})\s*H\s*({price_pattern})\s*L\s*({price_pattern})\s*C\s*({price_pattern})'
    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return None

    o_str = match.group(2)
    h_str = match.group(3)
    l_str = match.group(4)
    c_str = match.group(5)

    open_val = parse_price(o_str)
    high_val = parse_price(h_str)
    low_val = parse_price(l_str)
    close_val = parse_price(c_str)

    if None in (open_val, high_val, low_val, close_val):
        return None

    # Sanity check: low must be <= min(open, close) and max(open, close) <= high
    if low_val > min(open_val, close_val) or max(open_val, close_val) > high_val:
        return None

    return {
        'open': open_val,
        'high': high_val,
        'low': low_val,
        'close': close_val
    }


def parse_title(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Parse chart title: SYMBOL<sep>TF<sep>EXCHANGE or Bybit:SYMBOL.
    Sep can be -, ·, ., or space (one or more).
    Normalizes timeframe: digits -> Nm, 1h/4h kept as-is, 60->1h, D/1D->1d, O->0 in numbers.
    """
    if not text:
        return None

    text = text.strip()

    # Bybit format: Bybit:SYMBOL
    bybit_match = re.fullmatch(r'Bybit:([A-Z0-9]+)', text)
    if bybit_match:
        return {
            'symbol': bybit_match.group(1),
            'timeframe': None,
            'exchange': 'Bybit'
        }

    # Split by separators: -, ·, ., whitespace (one or more)
    parts = re.split(r'[-·.\s]+', text)
    if len(parts) != 3:
        return None

    symbol, tf_raw, exchange = parts

    # Validate symbol (alphanumeric and !)
    if not re.match(r'^[A-Z0-9!]+$', symbol):
        return None

    # Validate exchange (uppercase latin)
    if not re.match(r'^[A-Z]+$', exchange):
        return None

    # Normalize timeframe
    tf = tf_raw.upper().replace('O', '0')

    if tf == 'D' or tf == '1D':
        timeframe = '1d'
    elif tf == '60':
        timeframe = '1h'
    elif tf.endswith('H') and tf[:-1].isdigit():
        # 1H, 4H -> 1h, 4h
        timeframe = tf[:-1] + 'h'
    elif tf.isdigit():
        timeframe = tf + 'm'
    else:
        return None

    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'exchange': exchange
    }


def read_frame(path: str, *, ocr: Any = None) -> Dict[str, Union[str, float, List[str]]]:
    """
    Read chart header from image file.
    Crops header area (x: 21%-84% width, y: 0-7% height), upscales 3x with LANCZOS, runs OCR.
    Returns dict with symbol, exchange, timeframe, open, high, low, close, raw.
    Missing values are "UNKNOWN".
    """
    if ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()

    # numpy/PIL only for reading pixels: the parsers above stay importable without them (CI, Claude fix)
    import numpy as np
    from PIL import Image

    img = Image.open(path)
    width, height = img.size

    # Crop header: x 21% to 84%, y 0 to 7%
    left = int(width * 0.21)
    right = int(width * 0.84)
    top = 0
    bottom = int(height * 0.07)

    cropped = img.crop((left, top, right, bottom))

    # Upscale 3x
    new_size = (cropped.width * 3, cropped.height * 3)
    resized = cropped.resize(new_size, Image.Resampling.LANCZOS)

    # Convert to RGB numpy array
    img_array = np.array(resized)

    # Run OCR
    result, _ = ocr(img_array)

    info = {
        'symbol': 'UNKNOWN',
        'exchange': 'UNKNOWN',
        'timeframe': 'UNKNOWN',
        'open': 'UNKNOWN',
        'high': 'UNKNOWN',
        'low': 'UNKNOWN',
        'close': 'UNKNOWN',
        'raw': []
    }

    if result is None:
        return info

    for item in result:
        if len(item) < 2:
            continue
        text = item[1]
        info['raw'].append(text)

        # Try parse title if not found yet
        if info['symbol'] == 'UNKNOWN':
            title = parse_title(text)
            if title:
                info['symbol'] = title['symbol']
                info['exchange'] = title['exchange']
                info['timeframe'] = title['timeframe'] if title['timeframe'] is not None else 'UNKNOWN'

        # Try parse OHLC if not found yet
        if info['open'] == 'UNKNOWN':
            ohlc = parse_ohlc(text)
            if ohlc:
                info['open'] = ohlc['open']
                info['high'] = ohlc['high']
                info['low'] = ohlc['low']
                info['close'] = ohlc['close']

    return info


def ocr_block(info: Dict[str, Union[str, float, List[str]]]) -> str:
    """
    Format OCR info as a prompt string for the model.
    Format: OCR шапки графика: symbol=<..> exchange=<..> timeframe=<..> O=<..> H=<..> L=<..> C=<..> (last_close = C)
    """
    return (f"OCR шапки графика: symbol={info['symbol']} exchange={info['exchange']} "
            f"timeframe={info['timeframe']} O={info['open']} H={info['high']} "
            f"L={info['low']} C={info['close']} (last_close = C)")
