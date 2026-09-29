import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";
import { CONFIRM_TEXT, overrideLiveProblems, overrideProblems, readLiveListing, refusal } from "./guards.js";

export function registerOverrideTools(server: McpServer): void {

  server.registerTool("pricelabs_list_overrides", {
    title: "List Date-Specific Overrides",
    description: "Get all date-specific overrides (DSOs) for a listing — custom prices, min stays, min/max price overrides, and check-in/check-out restrictions by date.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name from list_listings (e.g. 'airbnb'; Hospitable uses 'smartbnb')"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms }) => {
    try {
      const res = await getPriceLabs().get(`/v1/listings/${encodeURIComponent(listing_id)}/overrides`, { params: { pms } });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_set_overrides", {
    title: "Set Date-Specific Overrides",
    description: "Create or update date-specific overrides (DSOs) for a listing — set custom prices (fixed or percent), min stays, min/max price bounds, and check-in/check-out day restrictions. Applies to this listing only (update_children is always sent as false). Guarded: every price needs its type, a fixed amount needs the listing's currency, a percent stays within -75 to 500, and a fixed night below the listing min is refused. Prefer the Revenue Manager's safe writer (fetch/apply_change.py) for revenue changes.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name from list_listings (e.g. 'airbnb'; Hospitable uses 'smartbnb')"),
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
      confirm: z.boolean().describe(CONFIRM_TEXT),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms, overrides, confirm }) => {
    if (confirm !== true) {
      return refusal("pricelabs_set_overrides", ["needs confirm: true, given only after the operator has seen the exact dates and amounts and said yes"]);
    }
    const shape = overrideProblems(overrides);
    if (shape.length) return refusal("pricelabs_set_overrides", shape);
    try {
      const http = getPriceLabs();
      const live = await readLiveListing(http, listing_id, pms);
      const against = overrideLiveProblems(overrides, live);
      if (against.length) return refusal("pricelabs_set_overrides", against);
      const res = await http.post(`/v1/listings/${encodeURIComponent(listing_id)}/overrides`, {
        overrides,
        pms,
        // Always explicit: never let a DSO silently cascade to child listings.
        update_children: false,
      });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_delete_overrides", {
    title: "Delete Date-Specific Overrides",
    description: "Remove date-specific overrides (DSOs) for specific dates. Optionally cascade deletion to child listings.",
    inputSchema: {
      listing_id: z.string().describe("Listing ID"),
      pms: z.string().describe("PMS name from list_listings (e.g. 'airbnb'; Hospitable uses 'smartbnb')"),
      overrides: z.array(z.object({
        date: z.string().describe("Date to remove override for (YYYY-MM-DD)"),
      })).describe("Array of dates to delete overrides for"),
      update_children: z.boolean().optional().describe("Also delete overrides from child listings (default false)"),
      confirm: z.boolean().describe("Must be true. Pass it only after the operator has seen the exact dates (and whether child listings are included) and said yes; the tool refuses otherwise."),
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id, pms, overrides, update_children, confirm }) => {
    if (confirm !== true) {
      return { isError: true, content: [{ type: "text", text: "Refused: pricelabs_delete_overrides needs confirm: true, given only after the operator has seen the exact dates and said yes. Nothing was deleted." }] };
    }
    try {
      const res = await getPriceLabs().delete(`/v1/listings/${encodeURIComponent(listing_id)}/overrides`, {
        data: { overrides, pms, update_children: update_children || false },
      });
      return { content: [{ type: "text", text: res.status === 204 ? "Overrides deleted successfully." : formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });
}
