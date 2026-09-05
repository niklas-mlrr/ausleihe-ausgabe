"""Token-/Code-/QR-Erzeugung für Modus-B-Sessions und Registrierungsflüsse.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring) — reine,
zustandslose Erzeugungsfunktionen ohne WebSocket-/IServ-Bezug. `sessions.py`
bleibt die öffentliche Fassade und re-exportiert alle Namen hier unverändert.
"""

from __future__ import annotations

import base64
import io
import secrets

import qrcode

from .state import AppState

# Gut ablesbares Alphabet (keine 0/O/1/I) für den iPad-Registrierungscode.
_REG_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ---------------------------------------------------------------------------
# Token-/Code-Erzeugung
# ---------------------------------------------------------------------------


def gen_session_token() -> str:
    """~256 bit Zufall — der eigentliche Zugangs-Credential."""
    return secrets.token_urlsafe(32)


def gen_join_secret() -> str:
    return secrets.token_urlsafe(16)


def gen_registration_code() -> str:
    return "".join(secrets.choice(_REG_ALPHABET) for _ in range(4))


def gen_pairing_code(state: AppState) -> str:
    """4-stelliger Code, eindeutig unter den aktiven pending-Sessions."""
    for _ in range(100):
        code = f"{secrets.randbelow(10000):04d}"
        if not state.code_in_use(code):
            return code
    raise RuntimeError("Kein freier Pairing-Code verfügbar")


def make_qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
