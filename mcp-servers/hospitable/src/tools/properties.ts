import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerPropertyTools(server: McpServer): void {

  server.registerTool("hospitable_list_properties", {
    title: "List Properties",
    description: "List all properties in your Hospitable account. Supports pagination and optional inclusion of listing platform data.",
    inputSchema: {
      include: z.string().optional().describe("Comma-separated related resources to include (e.g. 'listings')"),
      page: z.number().int().min(1).optional().describe("Page number for pagination"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page (max 100)"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ include, page, per_page }) => {
    try {
      const params: Record<string, unknown> = {};
      if (include) params.include = include;
      if (page) params.page = page;
      if (per_page) params.per_page = per_page;
      const res = await getHospitable().get("/properties", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_property", {
    title: "Get Property",
    description: "Get a single property by its UUID. Optionally include listing platform data.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
      include: z.string().optional().describe("Comma-separated related resources (e.g. 'listings')"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId, include }) => {
    try {
      const params: Record<string, unknown> = {};
      if (include) params.include = include;
      const res = await getHospitable().get(`/properties/${propertyId}`, { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_property_calendar", {
    title: "Get Property Calendar",
    description: "Get the calendar for a property — availability, pricing, minimum stays by date.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
      start_date: z.string().optional().describe("Start date (YYYY-MM-DD)"),
      end_date: z.string().optional().describe("End date (YYYY-MM-DD)"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId, start_date, end_date }) => {
    try {
      const params: Record<string, unknown> = {};
      if (start_date) params.start_date = start_date;
      if (end_date) params.end_date = end_date;
      const res = await getHospitable().get(`/properties/${propertyId}/calendar`, { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_update_property_calendar", {
    title: "Update Property Calendar",
    description: "Update calendar entries for a property — set pricing, availability, minimum stays for specific dates.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
      dates: z.array(z.object({
        date: z.string().describe("Date (YYYY-MM-DD)"),
        available: z.boolean().optional().describe("Whether the date is available"),
        price: z.number().optional().describe("Nightly price"),
        minimum_stay: z.number().int().optional().describe("Minimum stay nights"),
      })).describe("Array of date entries to update"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId, dates }) => {
    try {
      const res = await getHospitable().put(`/properties/${propertyId}/calendar`, { dates });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_property_images", {
    title: "Get Property Images",
    description: "Get all images for a property.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId }) => {
    try {
      const res = await getHospitable().get(`/properties/${propertyId}/images`);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_quote", {
    title: "Get Booking Quote",
    description: "Generate a booking quote for a property — returns pricing breakdown for the requested dates and guests.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
      check_in: z.string().describe("Check-in date (YYYY-MM-DD)"),
      check_out: z.string().describe("Check-out date (YYYY-MM-DD)"),
      guests: z.number().int().min(1).optional().describe("Number of guests"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId, check_in, check_out, guests }) => {
    try {
      const body: Record<string, unknown> = { check_in, check_out };
      if (guests) body.guests = guests;
      const res = await getHospitable().post(`/properties/${propertyId}/quote`, body);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
