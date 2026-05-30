"""Kalshi RSA-PSS signing: the signature must verify with the matching params."""
import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from liquidsky.kalshi_client import KalshiClient


def test_signature_verifies_with_pss_sha256():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    message = "1700000000000GET/trade-api/v2/portfolio/balance"

    sig_b64 = KalshiClient.sign_message(key, message)
    signature = base64.b64decode(sig_b64)

    # Verify with the exact PSS/SHA256/MGF1 parameters the client signs with.
    key.public_key().verify(
        signature,
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )  # raises InvalidSignature on mismatch


def test_signature_changes_with_message():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    a = KalshiClient.sign_message(key, "msg-a")
    b = KalshiClient.sign_message(key, "msg-b")
    assert a != b
