import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";

export function registerMarketTools(server: McpServer): void {

  server.registerTool("pricelabs_get_neighborhood_data", {
    title: "Get Neighborhood/Market Data",
    description: "Get comp set market data for a listing — base price percentiles by bedroom count, future price percentiles by date, future occupancy/new bookings/cancellations (current year + STLY), and Market KPIs (available days, booking window, LOS, revenue, booked days by month).",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name (e.g. 'airbnb', 'hospitable')"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms }) => {
    try {
      const res = await getPriceLabs().get("/v1/neighborhood_data", { params: { listing_id, pms } });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
