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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_update_property_calendar", {
    title: "Update Property Calendar",
    description:
      "Update calendar entries for a property: nightly price, availability, and minimum stay for specific dates. " +
      "UNITS: `price` is the nightly price in MAJOR currency units of the property's own calendar currency " +
      "(e.g. 285 means $285.00 USD/CAD, NOT cents). The read tool returns price.amount in minor units (cents), so " +
      "divide a read value by 100 before passing it here. The server converts to Hospitable's documented write shape " +
      "`price: { amount: <integer minor units> }`. Before writing, the server reads the live calendar and REFUSES any " +
      "price more than 20x above or below the current nightly price for that date (catches cents-vs-dollars mistakes), " +
      "and refuses when no current price can be read to compare against. Writes are processed asynchronously by Hospitable.",
    inputSchema: {
      propertyId: z.string().describe("Property UUID"),
      dates: z.array(z.object({
        date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).describe("Date (YYYY-MM-DD)"),
        available: z.boolean().optional().describe("Whether the date is available"),
        price: z.number().positive().optional().describe(
          "Nightly price in MAJOR units of the calendar currency (285 = $285.00). NOT cents. " +
          "Sent to Hospitable as price.amount in minor units. Refused if >20x or <1/20 of the current nightly price."),
        minimum_stay: z.number().int().min(1).optional().describe("Minimum stay nights (sent to Hospitable as min_stay)"),
      })).min(1).describe("Array of date entries to update"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ propertyId, dates }) => {
    try {
      const body = await buildCalendarWrite(propertyId, dates);
      const res = await getHospitable().put(`/properties/${propertyId}/calendar`, { dates: body });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
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
    } catch (e) { return { isError: true, content: [{ type: "text", text: handleError(e) }] }; }
  });
}

// Hospitable Money objects carry integer minor units (docs: "Currencies > Money
// object"; calendar writes accept `amount` only). Zero-decimal currencies have
// no cents, so the major->minor factor depends on the calendar currency.
const ZERO_DECIMAL = new Set(["BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"]);
const MAX_RATIO = 20;

type CalendarInput = { date: string; available?: boolean; price?: number; minimum_stay?: number };
type CalendarDay = { date?: string; price?: { amount?: number; currency?: string } };

export async function buildCalendarWrite(propertyId: string, dates: CalendarInput[]) {
  const priced = dates.filter((d) => d.price !== undefined);
  const current = new Map<string, { amount: number; currency: string }>();
  if (priced.length) {
    const sorted = priced.map((d) => d.date).sort();
    const res = await getHospitable().get(`/properties/${propertyId}/calendar`, {
      params: { start_date: sorted[0], end_date: sorted[sorted.length - 1] },
    });
    const days: CalendarDay[] = res.data?.data?.days ?? [];
    for (const day of days) {
      const amount = day.price?.amount;
      if (day.date && typeof amount === "number" && amount > 0 && day.price?.currency) {
        current.set(day.date, { amount, currency: day.price.currency });
      }
    }
  }
  return dates.map((d) => {
    const out: Record<string, unknown> = { date: d.date };
    if (d.available !== undefined) out.available = d.available;
    if (d.minimum_stay !== undefined) out.min_stay = d.minimum_stay;
    if (d.price !== undefined) {
      const now = current.get(d.date);
      if (!now) {
        throw new Error(`Refusing price write for ${d.date}: could not read a current nightly price to sanity-check against. Nothing was written.`);
      }
      const factor = ZERO_DECIMAL.has(now.currency.toUpperCase()) ? 1 : 100;
      const amount = Math.round(d.price * factor);
      const ratio = amount / now.amount;
      if (ratio > MAX_RATIO || ratio < 1 / MAX_RATIO) {
        const currentMajor = now.amount / factor;
        throw new Error(
          `Refusing price write for ${d.date}: ${d.price} ${now.currency} is ${ratio.toFixed(2)}x the current nightly ` +
          `price ${currentMajor} ${now.currency}. \`price\` must be in major units (dollars, not cents). Nothing was written.`);
      }
      out.price = { amount };
    }
    return out;
  });
}
