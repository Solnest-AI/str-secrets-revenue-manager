import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";

export function registerReservationTools(server: McpServer): void {

  server.registerTool("pricelabs_list_reservations", {
    title: "List Reservations",
    description: "Get reservations from PMS via PriceLabs — includes check-in/out, revenue, booking channel, cleaning fees, and cancellation data. Paginate with offset until next_page is false.",
    inputSchema: {
      pms: z.string().optional().describe("Filter by PMS name"),
      start_date: z.string().optional().describe("Filter reservations from this date (YYYY-MM-DD)"),
      end_date: z.string().optional().describe("Filter reservations to this date (YYYY-MM-DD)"),
      limit: z.number().int().min(1).max(500).optional().describe("Results per page (default 100)"),
      offset: z.number().int().min(0).optional().describe("Pagination offset"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ pms, start_date, end_date, limit, offset }) => {
    try {
      const params: Record<string, unknown> = {};
      if (pms) params.pms = pms;
      if (start_date) params.start_date = start_date;
      if (end_date) params.end_date = end_date;
      if (limit !== undefined) params.limit = limit;
      if (offset !== undefined) params.offset = offset;
      const res = await getPriceLabs().get("/v1/reservation_data", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
