"""
Open/click tracking — serves a 1x1 pixel and records opens.
"""
from __future__ import annotations

from outreach.db import record_open


# Transparent 1x1 GIF (43 bytes)
TRACKING_PIXEL = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00"
    b"\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)


def handle_open(sent_email_id: int) -> bytes:
    """Record the open event and return the tracking pixel."""
    try:
        record_open(sent_email_id)
    except Exception:
        pass  # Don't fail on tracking errors
    return TRACKING_PIXEL
