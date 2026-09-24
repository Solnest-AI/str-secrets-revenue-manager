"""Provider adapters: bounded reads, normalized storage and complete pagination."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import quote, urlencode, urlsplit

from _calendar import pricelabs_status, validate_calendar
from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_rankbreeze import parse_booking_funnel
from _mvp_store import CannotAnalyze, identity
from factcheck import neighborhood_base_percentiles, neighborhood_daily_from_raw
from reduce_customizations import ALL_RULES, normalize_rules
from reduce_prices import payload_matches, split_payload

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
PMS = "https://public.api.hospitable.com/v2"
PL = "https://api.pricelabs.co"


class MarketRolledOver(CannotAnalyze):
    """PriceLabs' market data starts exactly one day after the requested start and covers
    everything after it. That is the UTC-midnight rollover (measured 2026-09-24 ~06:15 UTC:
    the property was on the 23rd, PriceLabs on the 24th). The loader still refuses and never
    shifts the window itself; the runner decides what to do with a named rollover."""


class Sources:
    def __init__(self, client, connections):
        self.client = client
        self.connections = connections

    def pms_get(self, path, params=None):
        body, _ = self.client.request(
            "hospitable",
            path.split("/")[-1],
            PMS + path + ("?" + urlencode(params, doseq=True) if params else ""),
            headers={
                "Authorization": "Bearer " + self.connections.key("hospitable"),
                "Accept": "application/json",
            },
        )
        if not isinstance(body, dict) or "data" not in body:
            raise CannotAnalyze("Hospitable returned no data envelope")
        return body

    def pages(self, path, params, normalize, *, limit=None):
        rows, seen = [], set()
        total = None
        for page in range(1, 501):
            raw = self.pms_get(path, {**params, "per_page": 100, "page": page})
            if not isinstance(raw["data"], list):
                raise CannotAnalyze("Hospitable collection is not a list")
            meta = raw.get("meta", {})
            last = meta.get("last_page")
            if (
                type(last) is not int
                or last < page
                or type(meta.get("current_page")) is not int
                or meta["current_page"] != page
            ):
                raise CannotAnalyze("Hospitable pagination metadata is missing or inconsistent")
            if total is not None and meta.get("total") != total:
                raise CannotAnalyze("Hospitable collection changed during pagination; rerun")
            total = meta.get("total")
            if type(total) is not int or total < 0:
                raise CannotAnalyze("Hospitable total record count is unreadable")
            for record in raw["data"]:
                item = normalize(record)
                key = item.get("id")
                if not isinstance(key, str) or not key.strip() or key in seen:
                    raise CannotAnalyze("Missing or duplicate Hospitable record ID across pages")
                seen.add(key)
                rows.append(item)
            if page == last or (limit and len(rows) >= limit):
                complete = page == last
                if complete and len(rows) != total:
                    raise CannotAnalyze("Hospitable pagination count does not match its total")
                return {"data": rows, "total": total, "complete": complete, "pages": page}
        raise CannotAnalyze("Hospitable pagination exceeds safety limit")

    def property(self, selector):
        account = self.connections.account("hospitable")

        def load():
            if re.fullmatch(r"[a-fA-F0-9-]{36}", selector):
                item = normalize_property(
                    self.pms_get("/properties/" + selector, {"include": "listings"})["data"]
                )
                if item["id"] != selector:
                    raise CannotAnalyze("PMS returned another property")
            else:
                inventory = self.pages("/properties", {"include": "listings"}, normalize_property)
                matches = [
                    x
                    for x in inventory["data"]
                    if str(x.get("name", "")).casefold() == selector.casefold()
                ]
                if len(matches) != 1:
                    raise CannotAnalyze("Property name must match exactly one Hospitable property")
                item = matches[0]
            if item.get("listed") is False:
                raise CannotAnalyze("The selected PMS property is not listed")
            return item

        return self.client.fetch("pms.property", [account, selector], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        return self.client.fetch(
            "pms.calendar",
            [self.connections.account("hospitable"), pid, start.isoformat(), days],
            lambda: normalize_calendar(
                self.pms_get(
                    "/properties/" + pid + "/calendar",
                    {"start_date": start.isoformat(), "end_date": end.isoformat()},
                )["data"]
            ),
        )

    def reservations(self, pid, start, days):
        def normalize(raw):
            row = normalize_reservation(raw)
            if pid not in row.get("property_ids", []):
                raise CannotAnalyze("Reservation property scope could not be verified")
            return row

        return self.client.fetch(
            "pms.reservations",
            [self.connections.account("hospitable"), pid, start.isoformat(), days],
            lambda: self.pages(
                "/reservations",
                {
                    "properties[]": [pid],
                    "start_date": "2000-01-01",
                    # Pickup includes bookings made now for stays beyond this
                    # 90-day price calendar. Fetch one forward year once.
                    "end_date": (start + timedelta(days=max(days, 365))).isoformat(),
                    "include": "financials,properties",
                },
                normalize,
            ),
        )

    def reviews(self, pid):
        return self.client.fetch(
            "pms.reviews",
            [self.connections.account("hospitable"), pid],
            lambda: self.pages("/properties/" + pid + "/reviews", {}, normalize_review, limit=100),
        )

    def pl_get(self, path, params=None, body=None):
        result, _ = self.client.request(
            "pricelabs",
            "listing_prices" if body is not None else path.split("/")[-1],
            PL + path + ("?" + urlencode(params) if params else ""),
            headers={
                "X-API-Key": self.connections.key("pricelabs"),
                "User-Agent": UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            body=body,
        )
        return result

    def listing(self, lid, pms):
        def load():
            raw = self.pl_get("/v1/listings/" + quote(lid), {"pms": pms})
            rows = raw.get("listings") if isinstance(raw, dict) else None
            if not isinstance(rows, list) or len(rows) != 1 or str(rows[0].get("id")) != lid:
                raise CannotAnalyze("PriceLabs listing identity is not verified")
            item = rows[0]
            if item.get("pms") != pms:
                raise CannotAnalyze("PriceLabs PMS mapping does not match")
            fields = (
                "id",
                "pms",
                "name",
                "currency",
                "no_of_bedrooms",
                "latitude",
                "longitude",
                "min",
                "base",
                "max",
                "push_enabled",
                "last_refreshed_at",
                "last_date_pushed",
            )
            return {k: item.get(k) for k in fields}

        return self.client.fetch(
            "prices.metadata", [self.connections.account("pricelabs"), lid, pms], load
        )

    def prices(self, lid, pms, currency, start, days):
        end = start + timedelta(days=days - 1)

        def load():
            raw = self.pl_get(
                "/v1/listing_prices",
                body={
                    "listings": [
                        {
                            "id": lid,
                            "pms": pms,
                            "dateFrom": start.isoformat(),
                            "dateTo": end.isoformat(),
                        }
                    ]
                },
            )
            if not payload_matches(raw, [(lid, pms)]):
                raise CannotAnalyze("PriceLabs price response belongs to another listing")
            envelope = raw[0] if isinstance(raw, list) else raw
            if envelope.get("currency") != currency:
                raise CannotAnalyze("PriceLabs price currency does not match PMS")
            by_id, errors = split_payload(raw)
            if errors or lid not in by_id:
                raise CannotAnalyze("PriceLabs did not return a complete price calendar")
            rows = by_id[lid]
            validate_calendar(
                rows, "PriceLabs", pricelabs_status, start.isoformat(), end.isoformat()
            )
            fields = (
                "date",
                "price",
                "uncustomized_price",
                "min_stay",
                "booking_status",
                "unbookable",
                "demand_desc",
            )
            return {
                "last_refreshed_at": envelope.get("last_refreshed_at"),
                "data": [{k: r.get(k) for k in fields} for r in rows],
            }

        return self.client.fetch(
            "prices",
            [self.connections.account("pricelabs"), lid, pms, currency, start.isoformat(), days],
            load,
        )

    def neighborhood(self, lid, pms, bedrooms, currency, start, days, refresh=False):
        def load():
            raw = self.pl_get("/v1/neighborhood_data", {"listing_id": lid, "pms": pms})
            data = raw.get("data") if isinstance(raw, dict) else None
            if not isinstance(data, dict) or data.get("currency") != currency:
                raise CannotAnalyze("Neighborhood data is missing or its currency differs from PMS")
            if isinstance(bedrooms, bool) or float(bedrooms) < 0:
                raise CannotAnalyze("Invalid property bedroom count")
            category = format(float(bedrooms), "g")
            cats = data.get("Future Percentile Prices", {}).get("Category", {})
            if category not in cats:
                raise CannotAnalyze("Exact bedroom category is absent from neighborhood comps")
            rows = neighborhood_daily_from_raw({"data": data}, category, days, start.isoformat())
            expected = [(start + timedelta(days=i)).isoformat() for i in range(days)]
            if [x.get("date") for x in rows] != expected:
                observed = [x.get("date") for x in rows]
                missing = [day for day in expected if day not in observed]
                detail = (
                    f"missing {len(missing)} requested date(s): {', '.join(missing[:3])}"
                    if missing
                    else "has duplicate or unordered dates"
                )
                coverage = f"{observed[0]} to {observed[-1]}" if observed else "no dates"
                message = f"Neighborhood {detail}; provider covers {coverage}"
                if missing == expected[:1] and observed[: len(expected) - 1] == expected[1:]:
                    raise MarketRolledOver(message)
                raise CannotAnalyze(message)
            for row in rows:
                for key in ("p50", "p75", "p90", "occ"):
                    if row.get(key) in (None, ""):
                        raise CannotAnalyze("Required neighborhood price or occupancy is missing")
            return {
                "currency": currency,
                "category": category,
                "listings_used": cats[category].get("Listings Used"),
                "base_percentiles": neighborhood_base_percentiles(data, category),
                "data": rows,
            }

        return self.client.fetch(
            "neighborhood",
            [
                self.connections.account("pricelabs"),
                lid,
                pms,
                bedrooms,
                currency,
                start.isoformat(),
                days,
            ],
            load,
            ttl_seconds=0 if refresh else 86400,
        )

    def overrides(self, lid, pms, start, days):
        def load():
            raw = self.pl_get("/v1/listings/" + quote(lid) + "/overrides", {"pms": pms})
            rows = raw.get("overrides", raw.get("data")) if isinstance(raw, dict) else raw
            if not isinstance(rows, list) or any(
                not isinstance(r, dict) or not r.get("date") for r in rows
            ):
                raise CannotAnalyze("Unreadable overrides; absence cannot be assumed")
            end = (start + timedelta(days=days)).isoformat()
            fields = (
                "date",
                "price",
                "price_type",
                "min_price",
                "max_price",
                "min_stay",
                "created_at",
                "updated_at",
            )
            return [
                {k: r[k] for k in fields if k in r}
                for r in rows
                if start.isoformat() <= r["date"] < end
            ]

        return self.client.fetch(
            "overrides",
            [self.connections.account("pricelabs"), lid, pms, start.isoformat(), days],
            load,
        )

    def rules(self, lid, pms):
        def load():
            raw = self.pl_get(
                "/v1/customizations/listing",
                {"listing_id": lid, "pms_name": pms, "toggled_on": "false"},
            )
            rules = raw.get("customizations") if isinstance(raw, dict) else None
            if not isinstance(rules, dict) or not all(k in rules for k in ALL_RULES):
                raise CannotAnalyze(
                    "PriceLabs did not return all customization rules, including OFF rules"
                )

            # Free-text profile names are unnecessary for numeric attribution.
            def compact(value):
                if isinstance(value, dict):
                    return {
                        k: compact(v)
                        for k, v in value.items()
                        if k not in {"season_name", "name", "description", "notes"}
                    }
                if isinstance(value, list):
                    return [compact(x) for x in value]
                return value

            rules = {k: compact(rules[k]) for k in ALL_RULES}
            return {"raw": rules, "summary": normalize_rules(rules)}

        return self.client.fetch("rules", [self.connections.account("pricelabs"), lid, pms], load)

    def pile(self, lid, pms):
        """PRD D14: the PriceLabs pile, actions and nudges, fetched EVERY run.

        Both endpoints are ACCOUNT-WIDE. They return every listing's rows under whatever
        listing you asked about; measured live, 7 of 8 actions and the only nudge on
        one pull belonged to other properties. The reducer's flatten_* functions label
        each row this-listing / OTHER-LISTING, and we keep the strays, labelled, because
        the stored table should be an honest picture of what PriceLabs said.

        One input among many. Never the basis of the analysis (D14b).
        """
        from reduce_customizations import (ACTION_COLUMNS, NUDGE_COLUMNS,
                                           flatten_actions, flatten_nudges)

        def load():
            actions = self.pl_get("/v1/actions")
            nudges = self.pl_get("/v1/nudges/available")
            if not isinstance(nudges, dict) or "nudges" not in nudges:
                raise CannotAnalyze("PriceLabs nudges response carries no `nudges` "
                                    "collection; an error envelope is not an empty pile")
            action_rows = flatten_actions(actions, lid)
            nudge_rows = flatten_nudges(nudges, lid)
            mine_a = [r for r in action_rows if r[0] == "this-listing"]
            mine_n = [r for r in nudge_rows if r[0] == "this-listing"]
            return {
                "action_columns": ACTION_COLUMNS, "nudge_columns": NUDGE_COLUMNS,
                "actions": action_rows, "nudges": nudge_rows,
                "this_listing": {
                    "actions": [dict(zip(ACTION_COLUMNS, r)) for r in mine_a],
                    "nudges": [dict(zip(NUDGE_COLUMNS, r)) for r in mine_n],
                },
                "counts": {"actions": len(action_rows), "nudges": len(nudge_rows),
                           "actions_other": len(action_rows) - len(mine_a),
                           "nudges_other": len(nudge_rows) - len(mine_n)},
            }

        # Account-scoped cache key: the pile is the same for every listing on the account.
        return self.client.fetch("pile", [self.connections.account("pricelabs")], load)

    def funnel(self, rid, start):
        def load():
            raw, _ = self.client.request(
                "rankbreeze",
                "booking_funnel",
                "https://app.rankbreeze.com/rankings/" + quote(rid) + "/booking_funnel",
                headers={
                    "Cookie": "_godzilla_session=" + self.connections.key("rankbreeze"),
                    "User-Agent": UA,
                    "Accept": "text/html",
                    "X-Requested-With": "XMLHttpRequest",
                },
                text=True,
            )
            return parse_booking_funnel(raw, start, expected_listing_id=rid)

        return self.client.fetch(
            "rankbreeze.funnel",
            [self.connections.account("rankbreeze"), rid, start.isoformat()],
            load,
        )

    def rankings(self, rid, start, guest_capacity=1):
        url = self.connections.rankbreeze_url()
        if not url or urlsplit(url).scheme != "https":
            raise CannotAnalyze(
                "A hosted RankBreeze connection is required for dated ranking evidence"
            )
        session = None
        counter = 0

        def rpc(method, params):
            nonlocal session, counter
            counter += 1
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if session:
                headers["Mcp-Session-Id"] = session
            text, response_headers = self.client.request(
                "rankbreeze",
                "rpc",
                url,
                headers=headers,
                body={"jsonrpc": "2.0", "id": counter, "method": method, "params": params},
                text=True,
            )
            session = next(
                (v for k, v in response_headers.items() if k.lower() == "mcp-session-id"), session
            )
            if text.lstrip().startswith("{"):
                result = json.loads(text)
            else:
                events = [
                    json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")
                ]
                result = next((x for x in reversed(events) if x.get("id") == counter), {})
            if result.get("error") or "result" not in result:
                raise CannotAnalyze("RankBreeze RPC returned an error")
            return result["result"]

        def load():
            rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "revenue-manager-analysis", "version": "1"},
                },
            )
            if (
                isinstance(guest_capacity, bool)
                or float(guest_capacity) % 1
                or not 1 <= float(guest_capacity) <= 100
            ):
                raise CannotAnalyze("Property guest capacity is unreadable")
            required_guests = set(range(1, int(guest_capacity) + 1))
            arguments = {
                "listing_id": int(rid),
                "ranking_type": "daily",
                "date": start.isoformat(),
                "limit": 60,
            }
            current, seen_pages = [], set()
            for _ in range(10):
                result = rpc("tools/call", {"name": "get_listing_rankings", "arguments": arguments})
                if result.get("isError"):
                    raise CannotAnalyze("RankBreeze ranking tool refused the read")
                texts = [x["text"] for x in result.get("content", []) if x.get("type") == "text"]
                if len(texts) != 1:
                    raise CannotAnalyze("Unreadable RankBreeze ranking response")
                raw = json.loads(texts[0])
                rows = raw.get("rankings")
                if not isinstance(rows, list) or not rows:
                    break
                for entry in [raw, *rows]:
                    if entry.get("listing_id") is not None and str(entry["listing_id"]) != rid:
                        raise CannotAnalyze("RankBreeze returned a foreign listing identity")
                page_key = identity(rows)
                if page_key in seen_pages:
                    raise CannotAnalyze("RankBreeze pagination repeated a page")
                seen_pages.add(page_key)
                # The hosted API can ignore date filters. Verify exact current-date
                # guest coverage, rather than downloading unrelated historical pages.
                current.extend(r for r in rows if r.get("date") == start.isoformat())
                covered = {
                    int(r["guest_count"])
                    for r in current
                    if str(r.get("guest_count", "")).isdigit()
                }
                if required_guests <= covered:
                    fields = ("date", "guest_count", "position", "page")
                    return [
                        {k: r.get(k) for k in fields}
                        for r in current
                        if str(r.get("guest_count", "")).isdigit()
                        and int(r["guest_count"]) in required_guests
                    ]
                cursor = raw.get("nextCursor")
                if not cursor:
                    break
                arguments = {**arguments, "cursor": cursor}
            raise CannotAnalyze("Current rankings do not cover the property's guest counts")

        return self.client.fetch(
            "rankbreeze.rankings", [identity(url), rid, start.isoformat(), guest_capacity], load
        )
