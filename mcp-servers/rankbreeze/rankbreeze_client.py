"""
RankBreeze client — authenticated via browser session-cookie replay.

RankBreeze (app.rankbreeze.com) is a Rails/Devise app with no public API. We
authenticate by replaying the user's `_godzilla_session` cookie (the same
pattern used for the Skool/Loom MCPs) and parse the server-rendered HTML /
Turbo fragments the dashboard itself uses.

Cookie source (in priority order):
  1. env var RANKBREEZE_SESSION
  2. session.txt next to this file
Refresh it from Chrome DevTools → Application → Cookies → app.rankbreeze.com.
"""
import os
import re
import html
import statistics
import httpx

BASE = "https://app.rankbreeze.com"
DIR = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MONTHS = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_ACRONYMS = {"Uhnbc": "UHNBC", "Str": "STR", "Bc": "BC", "Ev": "EV", "Tv": "TV"}


class AuthError(RuntimeError):
    """Raised when the session cookie is missing or expired."""


# ----------------------------------------------------------------------------- transport
def _session() -> str:
    s = os.environ.get("RANKBREEZE_SESSION", "").strip()
    if not s:
        p = os.path.join(DIR, "session.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                s = fh.read().strip()
    if not s:
        raise AuthError(
            "No RankBreeze session cookie. Set RANKBREEZE_SESSION or put the "
            "_godzilla_session value in session.txt."
        )
    return s


def _get(path: str, ajax: bool = False) -> str:
    headers = {
        "Cookie": f"_godzilla_session={_session()}",
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=40) as c:
        r = c.get(path, headers=headers)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            if "sign_in" in loc:
                raise AuthError(
                    "RankBreeze session expired — refresh the _godzilla_session "
                    "cookie in session.txt (Chrome DevTools → Application → Cookies)."
                )
            r = c.get(loc, headers=headers)
    if r.status_code == 404:
        raise RuntimeError(f"Not found: {path} (bad listing id?)")
    r.raise_for_status()
    return r.text


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _pretty(title: str) -> str:
    out = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), title)
    for bad, good in _ACRONYMS.items():
        out = re.sub(rf"\b{bad}\b", good, out)
    return out


def _page_title(src: str) -> str:
    m = re.search(r"<title>([^<]*)</title>", src)
    return m.group(1).strip() if m else None


# ----------------------------------------------------------------------------- tools
def health_check() -> dict:
    """Confirm the cookie authenticates; return the logged-in account."""
    src = _get("/")
    body = re.search(r"<body([^>]*)>", src)
    a = dict(re.findall(r'data-([a-z0-9-]+)="([^"]*)"', body.group(1))) if body else {}
    email = re.search(r'"email":"([^"]+)"', src)
    return {
        "authenticated": True,
        "user_id": a.get("user-id"),
        "name": a.get("user-name"),
        "email": email.group(1) if email else None,
        "active_listings": a.get("user-active-listings-count"),
        "max_listings": a.get("user-max-listings-count"),
        "airbnb_integration_status": a.get("user-airbnb-integration-status"),
    }


def list_properties() -> dict:
    """All listings tracked in RankBreeze, plus account/plan info."""
    src = _get("/")
    body = re.search(r"<body([^>]*)>", src)
    a = dict(re.findall(r'data-([a-z0-9-]+)="([^"]*)"', body.group(1))) if body else {}
    kw = [(m.start(), m.group(1)) for m in re.finditer(r'data-filter-keywords="([^"]*)"', src)]
    ids, seen = [], set()
    for m in re.finditer(r"/rankings/(\d+)", src):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append((m.start(), m.group(1)))
    listings = []
    for pos, lid in ids:
        keywords = next((v for p, v in reversed(kw) if p < pos), "").strip()
        # Proper-case title only exists on the listing pages; the pricing page is the
        # lightest. Location is then whatever trails the title in the search keywords.
        try:
            title = _page_title(_get(f"/pricing/{lid}"))
        except Exception:
            title = None
        if title and keywords.startswith(title.lower()):
            loc = keywords[len(title):].strip(" -")
        else:
            m = re.search(r"([a-z][\w .]+ - [a-z][\w ]+ - [a-z]+)\s*$", keywords)
            loc = m.group(1) if m else None
            title = title or _pretty(keywords)
        listings.append({
            "id": lid,
            "name": title or f"Listing {lid}",
            "location": _pretty(", ".join(p.strip() for p in loc.split(" - "))) if loc else None,
        })
    return {
        "account": {
            "user_id": a.get("user-id"),
            "name": a.get("user-name"),
            "active_listings": a.get("user-active-listings-count"),
            "max_listings": a.get("user-max-listings-count"),
            "market_reports_used": a.get("user-market-reports-count"),
            "market_reports_max": a.get("user-max-market-reports-count"),
            "airbnb_integration_status": a.get("user-airbnb-integration-status"),
        },
        "listings": listings,
    }


def get_rankings(listing_id: str) -> dict:
    """Search-ranking position + booking-funnel performance vs similar listings."""
    bf = _clean(_get(f"/rankings/{listing_id}/booking_funnel", ajax=True))
    res = {"listing_id": str(listing_id)}
    c = re.search(r"Average city rankings\s+(\d+)\s+of\s+(\d+)\s+\(Page\s+(\d+)\)", bf)
    if c:
        res["city_rank"] = {"position": int(c.group(1)), "of": int(c.group(2)), "page": int(c.group(3))}
    pairs = {}
    for label, key in [("First page impressions", "first_page_impressions"),
                       ("Click-through rate", "click_through_rate"),
                       ("Wishlist additions", "wishlist_additions"),
                       ("Booking rate", "booking_rate"),
                       ("Overall conversion", "overall_conversion")]:
        m = re.search(re.escape(label) + r"\s+([\-\d.,]+%?)\s+Explore\s+[A-Za-z ]+?\s+[\-\d.,]+%?\s+([\-\d.,]+%?)", bf)
        if m:
            pairs[key] = {"you": m.group(1), "similar_listings": m.group(2)}
    res["funnel_vs_similar"] = pairs
    return res


def get_metrics(listing_id: str) -> dict:
    """Monthly performance trends (impressions, CTR, views, occupancy, ADR, revenue…)."""
    raw = _get(f"/rankings/{listing_id}/metric_cards", ajax=True)
    i = raw.find('replaceWith("')
    body = raw[i + 12:] if i >= 0 else raw
    body = body.replace("\\n", " ").replace('\\"', '"').replace("\\/", "/")
    txt = _clean(body)
    known = ["1st Page Impressions", "Click-through rate", "Listing views", "Wishlists",
             "Booking rate", "Conversion rates", "Airbnb Occupancy", "Avg. Daily Rates", "Revenue"]
    out = {}
    for seg in txt.split("More >"):
        mons = list(re.finditer(rf"\b({MONTHS})\b:?\s*(\$?[\d.,]+%?)\s*(imp|views|nights?|reviews?)?", seg))
        if not mons:
            continue
        head = seg[: mons[0].start()]
        label = next((k for k in known if k.lower() in head.lower()), None)
        if not label:
            continue
        series = {}
        for m in mons:
            unit = m.group(3) or ""
            series[m.group(1)] = f"{m.group(2)}{(' ' + unit) if unit else ''}".strip()
        out.setdefault(label, series)
    return {"listing_id": str(listing_id), "monthly_trends": out}


def get_calendar_rankings(listing_id: str) -> dict:
    """Per-date / per-guest-count search rankings + the 1st-page competitors' price & rating."""
    cr = _clean(_get(f"/rankings/{listing_id}/calendar_rankings", ajax=True))
    ranks = [(int(a), int(b), int(p)) for a, b, p in re.findall(r"#(\d+)/(\d+)\s*\(Page\s*(\d+)\)", cr)]
    prices = [int(x.replace(",", "")) for x in re.findall(r"Avg Price:\s*\$\s*([\d,]+)\s*/night", cr)]
    ratings = [float(x) for x in re.findall(r"Avg Rating:\s*([\d.]+)\s*/\s*5", cr)]
    res = {"listing_id": str(listing_id), "cells": len(ranks)}
    if ranks:
        best = min(ranks, key=lambda r: (r[2], r[0]))
        worst = max(ranks, key=lambda r: (r[2], r[0]))
        res["best"] = {"position": best[0], "of": best[1], "page": best[2]}
        res["worst"] = {"position": worst[0], "of": worst[1], "page": worst[2]}
    if prices:
        res["first_page_competitor_price"] = {"min": min(prices), "max": max(prices), "median": int(statistics.median(prices))}
    if ratings:
        res["first_page_competitor_rating"] = {"min": min(ratings), "max": max(ratings), "median": statistics.median(ratings)}
    return res


def get_competitor_rates(listing_id: str) -> dict:
    """Summary of the forward competitor nightly-rate calendar."""
    # Dates/prices are split across table cells, so parse the cleaned text. The date
    # headers come first; the price grid follows. Prices render with a space thousands
    # separator and no currency symbol (e.g. "1 258" = 1258).
    txt = _clean(_get(f"/rankings/{listing_id}/competitor_rates?redirect_back=rankings", ajax=True))
    date_re = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w+\s+\d+,\s+20\d\d"
    dates = re.findall(date_re, txt)
    res = {"listing_id": str(listing_id), "days_tracked": len(set(dates))}
    if dates:
        res["date_span"] = {"from": dates[0], "to": dates[-1]}
    # NOTE: per-cell nightly prices render as ambiguous space-separated digits in this
    # grid ("1 258" could be $1,258 or a flag + $258), so a flat regex can't read them
    # safely. For reliable competitor pricing use get_calendar_rankings()
    # (first_page_competitor_price). A structured per-date parse is a future enhancement.
    res["competitor_prices"] = "see get_calendar_rankings for reliable 1st-page competitor pricing"
    return res


def analyze_property(listing_id: str) -> dict:
    """Full analysis for one listing: rankings, funnel, monthly trends, calendar, competitors."""
    out = {"listing_id": str(listing_id)}
    for key, fn in [("rankings", get_rankings), ("metrics", get_metrics),
                    ("calendar_rankings", get_calendar_rankings),
                    ("competitor_rates", get_competitor_rates)]:
        try:
            out[key] = fn(listing_id)
        except Exception as e:  # one failed section shouldn't kill the report
            out[key] = {"error": str(e)}
    return out
