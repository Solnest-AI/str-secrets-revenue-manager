import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";

export function registerPricingTools(server: McpServer): void {

  server.registerTool("pricelabs_get_listing_prices", {
    title: "Get Listing Prices",
    description: "Get per-date pricing for one or more listings — includes PriceLabs recommended price, user price (from PMS), demand level, ADR, booking status, min stay, discounts, and pricing reason when enabled.",
    inputSchema: {
      listings: z.array(z.object({
        id: z.string().describe("Listing ID"),
        pms: z.string().describe("PMS name (e.g. 'airbnb', 'hospitable')"),
        dateFrom: z.string().describe("Start date (YYYY-MM-DD) — current or future dates only"),
        dateTo: z.string().describe("End date (YYYY-MM-DD)"),
        reason: z.boolean().optional().describe("Include pricing reason/explanation (default false)"),
      })).describe("Array of listings to get prices for"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listings }) => {
    try {
      const res = await getPriceLabs().post("/v1/listing_prices", { listings });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_get_rate_plans", {
    title: "Get Rate Plans",
    description: "Get rate plan adjustments for listings — rate plan names, types, adjustment percentages. Use with listing prices to derive non-default rate plan prices.",
    inputSchema: {
      listing_id: z.string().optional().describe("Filter by listing ID"),
      pms_name: z.string().optional().describe("Filter by PMS name"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms_name }) => {
    try {
      const params: Record<string, unknown> = {};
      if (listing_id) params.listing_id = listing_id;
      if (pms_name) params.pms_name = pms_name;
      const res = await getPriceLabs().get("/v1/fetch_rate_plans", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
