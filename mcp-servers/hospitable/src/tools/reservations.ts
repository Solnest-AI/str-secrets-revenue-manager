import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerReservationTools(server: McpServer): void {

  server.registerTool("hospitable_list_reservations", {
    title: "List Reservations",
    description: "List upcoming/active reservations with optional filters. Returns guest details, dates, status, and platform info. NOTE: The Hospitable API only returns upcoming and active reservations — past/completed reservations are not available through this endpoint. For historical reservation data, use hospitable_list_transactions instead.",
    inputSchema: {
      properties: z.array(z.string()).optional().describe("Array of property UUIDs to filter by. If omitted, all properties on your connected account are queried automatically."),
      property_id: z.string().optional().describe("Single property UUID (convenience — added to properties array)"),
      status: z.array(z.string()).optional().describe("Filter by status array. Valid values: not_accepted, request, accepted, cancelled, checkpoint"),
      check_in_from: z.string().optional().describe("Filter reservations with check-in on or after this date (YYYY-MM-DD)"),
      check_in_to: z.string().optional().describe("Filter reservations with check-in on or before this date (YYYY-MM-DD)"),
      check_out_from: z.string().optional().describe("Filter reservations with check-out on or after this date (YYYY-MM-DD)"),
      check_out_to: z.string().optional().describe("Filter reservations with check-out on or before this date (YYYY-MM-DD)"),
      created_from: z.string().optional().describe("Filter by booking creation date from (YYYY-MM-DD)"),
      created_to: z.string().optional().describe("Filter by booking creation date to (YYYY-MM-DD)"),
      page: z.number().int().min(1).optional().describe("Page number"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async (params) => {
    try {
      const { properties: propsParam, property_id, status, ...rest } = params;

      // Build the properties list: explicit array > single property_id > all properties
      let propertyIds: string[];
      if (propsParam && propsParam.length > 0) {
        propertyIds = propsParam;
      } else if (property_id) {
        propertyIds = [property_id];
      } else {
        // Default: every property on the connected account, fetched live —
        // works for any operator, with no hardcoded property IDs.
        const propsRes = await getHospitable().get("/properties", { params: { per_page: 100 } });
        propertyIds = (propsRes.data?.data ?? []).map((p: { id: string }) => p.id).filter(Boolean);
      }

      const queryParams: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(rest)) {
        if (value !== undefined) queryParams[key] = value;
      }
      // Hospitable expects properties[] and status[] as repeated array params
      queryParams["properties[]"] = propertyIds;
      if (status && status.length > 0) {
        queryParams["status[]"] = status;
      }

      const res = await getHospitable().get("/reservations", { params: queryParams });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_reservation", {
    title: "Get Reservation",
    description: "Get a single reservation by UUID — full details including financials, guest info, and platform data.",
    inputSchema: {
      reservationId: z.string().describe("Reservation UUID"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ reservationId }) => {
    try {
      const res = await getHospitable().get(`/reservations/${reservationId}`);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_create_reservation", {
    title: "Create Reservation",
    description: "Create a new direct booking reservation.",
    inputSchema: {
      property_id: z.string().describe("Property UUID"),
      check_in: z.string().describe("Check-in date (YYYY-MM-DD)"),
      check_out: z.string().describe("Check-out date (YYYY-MM-DD)"),
      guest_first_name: z.string().describe("Guest first name"),
      guest_last_name: z.string().describe("Guest last name"),
      guest_email: z.string().optional().describe("Guest email"),
      guest_phone: z.string().optional().describe("Guest phone"),
      guests: z.number().int().min(1).optional().describe("Number of guests"),
      total_price: z.number().optional().describe("Total price"),
      currency: z.string().optional().describe("Currency code (e.g. USD)"),
      notes: z.string().optional().describe("Internal notes"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
  }, async (params) => {
    try {
      const res = await getHospitable().post("/reservations", params);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_update_reservation", {
    title: "Update Reservation",
    description: "Update an existing reservation's details.",
    inputSchema: {
      reservationId: z.string().describe("Reservation UUID"),
      check_in: z.string().optional().describe("New check-in date (YYYY-MM-DD)"),
      check_out: z.string().optional().describe("New check-out date (YYYY-MM-DD)"),
      guests: z.number().int().min(1).optional().describe("Updated guest count"),
      total_price: z.number().optional().describe("Updated total price"),
      notes: z.string().optional().describe("Updated internal notes"),
      status: z.string().optional().describe("Updated status"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ reservationId, ...updates }) => {
    try {
      const res = await getHospitable().patch(`/reservations/${reservationId}`, updates);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
