"""Booking (reservation) tools."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_bookings(
        properties: list[int] | None = None,
        checkin_from: str | None = None,
        checkout_from: str | None = None,
        checkin_to: str | None = None,
        checkout_to: str | None = None,
        sort: str | None = None,
        order: str | None = None,
        limit: int = 20,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List bookings (reservations). Filter by check-in/out date windows
        ('YYYY-MM-DD') and a properties id list. fetch_all=true walks all pages. GET /bookings."""
        params: dict[str, Any] = {
            "checkin_from": checkin_from,
            "checkout_from": checkout_from,
            "checkin_to": checkin_to,
            "checkout_to": checkout_to,
            "sort": sort,
            "order": order,
            "limit": limit,
            "page": page,
        }
        if properties:
            params["properties[]"] = properties  # Turno /bookings uses bracket-array encoding, not CSV
        client = get_client()
        if fetch_all:
            return await client.paginate("/bookings", params=params)
        return await client.get("/bookings", params=params)

    @mcp.tool
    async def turno_get_booking(booking_id: int) -> Any:
        """Get a single booking by id. GET /bookings/{id}."""
        return await get_client().get(f"/bookings/{booking_id}")

    @mcp.tool
    async def turno_create_booking(data: dict[str, Any]) -> Any:
        """Create a booking. `data` is the JSON body (e.g. property_id, check-in/out dates,
        guest details, external id). POST /bookings."""
        return await get_client().post("/bookings", json=data)

    @mcp.tool
    async def turno_update_booking(booking_id: int, data: dict[str, Any]) -> Any:
        """Update a booking. `data` holds fields to change. PATCH /bookings/{id}."""
        return await get_client().patch(f"/bookings/{booking_id}", json=data)

    @mcp.tool
    async def turno_delete_booking(booking_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: delete a booking. Requires confirm=true. DELETE /bookings/{id}."""
        require_confirm(confirm, "turno_delete_booking")
        return await get_client().delete(f"/bookings/{booking_id}")
