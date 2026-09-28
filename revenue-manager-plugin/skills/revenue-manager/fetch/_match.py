"""One property picker for every PMS adapter: `--property` by id, then name.

Order: an exact id, then an exact name (case-insensitive), then a loose name (accents,
punctuation and a leading "The" ignored: "apres arcade" finds "The Après Arcade"). Each step
must land on exactly ONE property or it does not count; a loose match never breaks a tie
the exact name left. On no match the error names the closest properties, so the operator
can say the right one instead of guessing (Windows first run, 2026-09-28).
"""

from __future__ import annotations

import difflib
import re
import unicodedata


def loose(name) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())
    return text[4:] if text.startswith("the ") else text


def _label(row, names) -> str:
    return next((str(n) for n in names(row) if n), "?")


def pick(rows, selector, *, message, error, ids=lambda r: (r.get("id"),), names=lambda r: (r.get("name"),)):
    """The one row `selector` names. Raises error(message [+ the closest names])."""
    want = str(selector)
    # A name with no Latin letters or digits reduces to "" and must never match another "".
    loose_want = loose(want)
    for hit in (lambda r: want in {str(i) for i in ids(r) if i is not None},
                lambda r: want.casefold() in {str(n).casefold() for n in names(r) if n},
                lambda r: bool(loose_want) and loose_want in {loose(n) for n in names(r) if n}):
        hits = [r for r in rows if hit(r)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise error(f"{message}; {len(hits)} match {selector!r}: "
                        + ", ".join(sorted({_label(r, names) for r in hits})))
    by_loose = {loose(n): str(n) for r in rows for n in names(r) if n and loose(n)}
    close = difflib.get_close_matches(loose_want, list(by_loose), n=3, cutoff=0.5) if loose_want else []
    raise error(message + (f"; closest: {', '.join(by_loose[c] for c in close)}" if close else ""))
