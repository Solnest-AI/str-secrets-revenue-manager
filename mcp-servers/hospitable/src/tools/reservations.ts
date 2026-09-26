import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerReservationTools(server: McpServer): void {

  server.registerTool("hospitable_list_reservations", {
    title: "List Reservations",
    description: "List reservations, including historical bookings when start_date and end_date are supplied. Request include='financials' for revenue details (requires financials:read scope). Supports property, status, date, and pagination filters.",
    inputSchema: {
      properties: z.array(z.string()).optional().describe("Array of property UUIDs to filter by. If omitted, all properties on your connected account are queried automatically."),
      property_id: z.string().optional().describe("Single property UUID (convenience — added to properties array)"),
      status: z.array(z.string()).optional().describe("Filter by status array. Valid values: not_accepted, request, accepted, cancelled, checkpoint"),
      start_date: z.string().optional().describe("Start of reservation date range (YYYY-MM-DD); set a past range for historical bookings"),
      end_date: z.string().optional().describe("End of reservation date range (YYYY-MM-DD)"),
      include: z.string().optional().describe("Comma-separated related resources, e.g. 'financials,guest,properties,listings'"),
      check_in_from: z.string().optional().describe("Legacy alias for start_date"),
      check_in_to: z.string().optional().describe("Legacy alias for end_date"),
      check_out_from: z.string().optional().describe("Unsupported legacy filter; use start_date/end_date instead"),
      check_out_to: z.string().optional().describe("Unsupported legacy filter; use start_date/end_date instead"),
      created_from: z.string().optional().describe("Unsupported legacy filter; use start_date/end_date instead"),
      created_to: z.string().optional().describe("Unsupported legacy filter; use start_date/end_date instead"),
      page: z.number().int().min(1).optional().describe("Page number"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async (params) => {
    try {
      const {
        properties: propsParam, property_id, status, start_date, end_date,
        check_in_from, check_in_to, check_out_from, check_out_to,
        created_from, created_to, ...rest
      } = params;
      const unsupported = { check_out_from, check_out_to, created_from, created_to };
      const unsupportedKeys = Object.keys(unsupported).filter((key) => unsupported[key as keyof typeof unsupported] !== undefined);
      if (unsupportedKeys.length) {
        throw new Error(`Unsupported reservation filters: ${unsupportedKeys.join(", ")}. Use start_date/end_date.`);
      }
      if ((start_date && check_in_from && start_date !== check_in_from) ||
          (end_date && check_in_to && end_date !== check_in_to)) {
        throw new Error("Conflicting reservation date filters. Use start_date/end_date without conflicting legacy aliases.");
      }

      // Build the properties list: explicit array > single property_id > all properties
      let propertyIds: string[];
      if (propsParam && propsParam.length > 0) {
        propertyIds = propsParam;
      } else if (property_id) {
        propertyIds = [property_id];
      } else {
        propertyIds = [];
        let propertyPage = 1;
        for (;;) {
          const propsRes = await getHospitable().get("/properties", { params: { page: propertyPage, per_page: 100 } });
          const properties = propsRes.data?.data ?? [];
          propertyIds.push(...properties.map((p: { id: string }) => p.id).filter(Boolean));
          const lastPage = propsRes.data?.meta?.last_page ?? propertyPage;
          if (properties.length === 0 || propertyPage >= lastPage) break;
          propertyPage++;
        }
      }

      const queryParams: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(rest)) {
        if (value !== undefined) queryParams[key] = value;
      }
      if (start_date ?? check_in_from) queryParams.start_date = start_date ?? check_in_from;
      if (end_date ?? check_in_to) queryParams.end_date = end_date ?? check_in_to;
      // Hospitable expects properties[] and status[] as repeated array params
      queryParams["properties[]"] = propertyIds;
      if (status && status.length > 0) {
        queryParams["status[]"] = status;
      }

      const res = await getHospitable().get("/reservations", { params: queryParams });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_reservation", {
    title: "Get Reservation",
    description: "Get a single reservation by UUID — full details including financials, guest info, and platform data.",
    inputSchema: {
      reservationId: z.string().describe("Reservation UUID"),
      include: z.string().optional().describe("Comma-separated related resources, e.g. 'financials,guest,properties,listings'"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ reservationId, include }) => {
    try {
      const res = await getHospitable().get(`/reservations/${reservationId}`, { params: include ? { include } : {} });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });
}
