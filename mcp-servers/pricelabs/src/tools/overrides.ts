import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";

export function registerOverrideTools(server: McpServer): void {

  server.registerTool("pricelabs_list_overrides", {
    title: "List Date-Specific Overrides",
    description: "Get all date-specific overrides (DSOs) for a listing — custom prices, min stays, min/max price overrides, and check-in/check-out restrictions by date.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name (e.g. 'airbnb', 'hospitable')"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms }) => {
    try {
      const res = await getPriceLabs().get(`/v1/listings/${listing_id}/overrides`, { params: { pms } });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_set_overrides", {
    title: "Set Date-Specific Overrides",
    description: "Create or update date-specific overrides (DSOs) for a listing — set custom prices (fixed or percent), min stays, min/max price bounds, and check-in/check-out day restrictions.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name (e.g. 'airbnb', 'hospitable')"),
      overrides: z.array(z.object({
        date: z.string().describe("Date (YYYY-MM-DD)"),
        price: z.number().optional().describe("Override price"),
        price_type: z.enum(["fixed", "percent"]).optional().describe("Price type — 'fixed' for absolute, 'percent' for % adjustment (-75 to 500)"),
        currency: z.string().optional().describe("Currency code (e.g. CAD, USD)"),
        min_stay: z.number().int().optional().describe("Minimum stay nights"),
        min_price: z.number().optional().describe("Minimum price floor"),
        min_price_type: z.enum(["fixed", "percent"]).optional().describe("Min price type"),
        max_price: z.number().optional().describe("Maximum price ceiling"),
        max_price_type: z.enum(["fixed", "percent"]).optional().describe("Max price type"),
        base_price: z.number().optional().describe("Base price override"),
        check_in_check_out_enabled: z.number().int().min(0).max(1).optional().describe("Enable check-in/out restrictions (0 or 1)"),
        check_in: z.string().optional().describe("7-char binary Mon-Sun (e.g. '1111100' = Mon-Fri only)"),
        check_out: z.string().optional().describe("7-char binary Mon-Sun (e.g. '0000011' = Sat-Sun only)"),
        reason: z.string().optional().describe("Reason for override (for your reference)"),
      })).describe("Array of date overrides to set"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms, overrides }) => {
    try {
      const res = await getPriceLabs().post(`/v1/listings/${listing_id}/overrides`, {
        overrides,
        pms,
      });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_delete_overrides", {
    title: "Delete Date-Specific Overrides",
    description: "Remove date-specific overrides (DSOs) for specific dates. Optionally cascade deletion to child listings.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name (e.g. 'airbnb', 'hospitable')"),
      overrides: z.array(z.object({
        date: z.string().describe("Date to remove override for (YYYY-MM-DD)"),
      })).describe("Array of dates to delete overrides for"),
      update_children: z.boolean().optional().describe("Also delete overrides from child listings (default false)"),
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms, overrides, update_children }) => {
    try {
      const res = await getPriceLabs().delete(`/v1/listings/${listing_id}/overrides`, {
        data: { overrides, pms, update_children: update_children || false },
      });
      return { content: [{ type: "text", text: res.status === 204 ? "Overrides deleted successfully." : formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
