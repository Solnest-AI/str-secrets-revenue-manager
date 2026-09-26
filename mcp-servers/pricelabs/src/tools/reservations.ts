import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";

export function registerReservationTools(server: McpServer): void {

  server.registerTool("pricelabs_list_reservations", {
    title: "List Reservations",
    description: "Get reservations from a PMS via PriceLabs. Requires pms and either start_date/end_date or a booked-date filter. An optional listing_id is enforced locally. Paginate with offset until next_page is false, even when a filtered page is empty.",
    inputSchema: {
      pms: z.string().trim().min(1).describe("PMS name from list_listings (Hospitable uses 'smartbnb')"),
      listing_id: z.string().min(1).optional().describe("Only return this listing's reservations; the connector checks every returned row"),
      start_date: z.string().optional().describe("Inclusive check-in date start (YYYY-MM-DD); requires end_date"),
      end_date: z.string().optional().describe("Exclusive check-in date end (YYYY-MM-DD); requires start_date"),
      booked_start_date: z.string().optional().describe("Inclusive booking date start (YYYY-MM-DD)"),
      booked_end_date: z.string().optional().describe("Inclusive booking date end (YYYY-MM-DD)"),
      limit: z.number().int().min(1).max(500).optional().describe("Results per page (default 100)"),
      offset: z.number().int().min(0).optional().describe("Pagination offset"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ pms, listing_id, start_date, end_date, booked_start_date, booked_end_date, limit, offset }) => {
    try {
      if (Boolean(start_date) !== Boolean(end_date)) {
        throw new Error("start_date and end_date must be supplied together.");
      }
      if (!start_date && !booked_start_date && !booked_end_date) {
        throw new Error("Provide start_date/end_date or booked_start_date and/or booked_end_date.");
      }
      const params: Record<string, unknown> = { pms };
      if (start_date) params.start_date = start_date;
      if (end_date) params.end_date = end_date;
      if (booked_start_date) params.booked_start_date = booked_start_date;
      if (booked_end_date) params.booked_end_date = booked_end_date;
      if (limit !== undefined) params.limit = limit;
      if (offset !== undefined) params.offset = offset;
      const res = await getPriceLabs().get("/v1/reservation_data", { params });
      let data = res.data;
      if (listing_id) {
        if (!Array.isArray(data?.data)) {
          throw new Error("Cannot filter reservations: expected a data array in the PriceLabs response.");
        }
        // The upstream endpoint can ignore listing_id. Keep its pagination
        // signal while enforcing the requested property on every page locally.
        data = { ...data, data: data.data.filter((row: { listing_id?: string | number }) => String(row.listing_id) === listing_id) };
      }
      return { content: [{ type: "text", text: formatResponse(data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });
}
