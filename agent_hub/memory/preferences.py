"""Cheap, deterministic preference detection on user Telegram messages.

We avoid LLM-based classification on purpose — it's a per-message overhead on
a hot path, and 'maybe a preference' is a soft signal. We let the user
confirm via inline-keyboard before anything lands in memory.
"""

from __future__ import annotations

import re


_PREFERENCE_MARKERS = re.compile(
    r"\b(don'?t|stop|never|always|from now on|prefer|please don'?t)\b",
    re.IGNORECASE,
)


def looks_like_preference(text: str) -> bool:
    """True if the text contains a corrective/imperative marker.

    False positives are fine — the user is asked to confirm before
    anything is written. False negatives mean a preference slips
    through unnoticed, which is recoverable via `/remember`.
    """
    if not text or text.startswith("/"):
        return False
    return _PREFERENCE_MARKERS.search(text) is not None
