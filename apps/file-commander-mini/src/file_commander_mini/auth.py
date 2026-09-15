"""Small standalone wire contract for the BCC-to-child boundary.

No bearer secret crosses a port. Requests bind method, raw path/query, body,
timestamp and a fresh nonce. Responses bind status and exact bytes to that nonce.
"""
import hashlib
import hmac
import re
import time

TTL = 30
SIGNATURE = "X-Bossman-App-Signature"
STAMP = "X-Bossman-App-Timestamp"
NONCE = "X-Bossman-App-Nonce"
RESPONSE = "X-Bossman-App-Response"


def request_mac(token, method, target, body, stamp, nonce):
    message = "\n".join(("request", method, target, stamp, nonce, hashlib.sha256(body).hexdigest()))
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def response_mac(token, nonce, status, body):
    message = "\n".join(("response", nonce, str(status), hashlib.sha256(body).hexdigest()))
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_request(token, headers, method, target, body):
    stamp, nonce, signature = headers.get(STAMP, ""), headers.get(NONCE, ""), headers.get(SIGNATURE, "")
    if not re.fullmatch(r"[0-9]{1,12}", stamp) or abs(time.time() - int(stamp)) > TTL:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", nonce):
        return None
    expected = request_mac(token, method, target, body, stamp, nonce)
    return (nonce, int(stamp)) if hmac.compare_digest(signature, expected) else None
