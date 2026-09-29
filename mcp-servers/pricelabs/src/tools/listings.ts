import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getPriceLabs, formatResponse, handleError } from "../services/pricelabs-client.js";
import { CONFIRM_TEXT, listingUpdateProblems, readLiveListing, refusal } from "./guards.js";

export function registerListingTools(server: McpServer): void {

  server.registerTool("pricelabs_list_listings", {
    title: "List Listings",
    description: "Get all listings in your PriceLabs account with pricing config, occupancy metrics, and sync status.",
    inputSchema: {
      skip_hidden: z.boolean().optional().describe("Skip hidden listings (default false)"),
      only_syncing_listings: z.boolean().optional().describe("Only return actively syncing listings (default false)"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ skip_hidden, only_syncing_listings }) => {
    try {
      const params: Record<string, unknown> = {};
      if (skip_hidden !== undefined) params.skip_hidden = skip_hidden;
      if (only_syncing_listings !== undefined) params.only_syncing_listings = only_syncing_listings;
      const res = await getPriceLabs().get("/v1/listings", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_get_listing", {
    title: "Get Listing",
    description: "Get a single listing by ID — pricing config (min/base/max), occupancy metrics, channel details, and sync status.",
    inputSchema: {
      listing_id: z.string().describe("PriceLabs listing ID"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listing_id }) => {
    try {
      const res = await getPriceLabs().get(`/v1/listings/${encodeURIComponent(listing_id)}`);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("pricelabs_update_listings", {
    title: "Update Listings",
    description: "Update min, base, and/or max prices and tags for one or more listings. Send any combination of min/base/max. Guarded: each listing is read live first, and min <= base <= max must hold after the update, every value must be above zero, and no value may move more than 5x from its live value. Prefer the Revenue Manager's safe writer (fetch/apply_change.py) for revenue changes.",
    inputSchema: {
      listings: z.array(z.object({
        id: z.string().describe("Listing ID"),
        pms: z.string().describe("PMS name from list_listings (e.g. 'airbnb'; Hospitable uses 'smartbnb')"),
        min: z.number().optional().describe("Minimum price"),
        base: z.number().optional().describe("Base price"),
        max: z.number().optional().describe("Maximum price"),
        tags: z.array(z.string()).max(10).optional().describe("Tags (max 10)"),
      })).describe("Array of listings to update"),
      confirm: z.boolean().describe(CONFIRM_TEXT),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ listings, confirm }) => {
    if (confirm !== true) {
      return refusal("pricelabs_update_listings", ["needs confirm: true, given only after the operator has seen the exact min/base/max and said yes"]);
    }
    if (listings.length === 0) return refusal("pricelabs_update_listings", ["no listings given"]);
    try {
      const http = getPriceLabs();
      const problems: string[] = [];
      for (const u of listings) {
        if (u.min === undefined && u.base === undefined && u.max === undefined) continue; // tags only
        problems.push(...listingUpdateProblems(u, await readLiveListing(http, u.id, u.pms)));
      }
      if (problems.length) return refusal("pricelabs_update_listings", problems);
      const res = await http.post("/v1/listings", { listings });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });
}
