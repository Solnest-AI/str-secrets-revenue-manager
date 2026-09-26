"""ISO 4217 minor units, shared by the readers and the writers.

The engine's `*_cents` fields mean hundredths of a major unit for every currency. Hospitable
sends money in the currency's own minor unit, which is only the same thing for two-decimal
currencies; the others are refused on that read path until they are converted end to end.
"""

from __future__ import annotations

ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX",
                          "UYI", "VND", "VUV", "XAF", "XOF", "XPF"})
THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def decimals(currency) -> int | None:
    """0, 2 or 3; None when the code is not a three-letter currency."""
    cur = str(currency or "").upper()
    if len(cur) != 3 or not cur.isalpha():
        return None
    return 0 if cur in ZERO_DECIMAL else 3 if cur in THREE_DECIMAL else 2
