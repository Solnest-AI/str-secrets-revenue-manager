import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerReviewTools(server: McpServer): void {

  server.registerTool("hospitable_list_reviews", {
    title: "List Reviews",
    description: "List guest reviews. Provide a property_id for a single property, or omit it to pull every review across all properties. The Hospitable API nests reviews under each property, so the all-properties mode fans out across properties and aggregates the results.",
    inputSchema: {
      property_id: z.string().optional().describe("Property UUID. Omit to aggregate reviews across all properties."),
      page: z.number().int().min(1).optional().describe("Page number (single-property mode only)"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page (single-property mode only)"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ property_id, page, per_page }) => {
    try {
      const client = getHospitable();

      if (property_id) {
        const params: Record<string, unknown> = {};
        if (page) params.page = page;
        if (per_page) params.per_page = per_page;
        const res = await client.get(`/properties/${property_id}/reviews`, { params });
        return { content: [{ type: "text", text: formatResponse(res.data) }] };
      }

      // No property_id: fan out across every property and aggregate.
      const properties: Array<{ id: string; name: string }> = [];
      let propPage = 1;
      for (;;) {
        const res = await client.get("/properties", { params: { page: propPage, per_page: 100 } });
        for (const p of res.data?.data ?? []) properties.push({ id: p.id, name: p.name });
        const lastPage = res.data?.meta?.last_page ?? propPage;
        if (propPage >= lastPage) break;
        propPage++;
      }

      const reviews: Array<Record<string, unknown>> = [];
      for (const prop of properties) {
        let revPage = 1;
        for (;;) {
          const res = await client.get(`/properties/${prop.id}/reviews`, {
            params: { page: revPage, per_page: 100 },
          });
          for (const r of res.data?.data ?? []) {
            reviews.push({ ...r, property_id: prop.id, property_name: prop.name });
          }
          const lastPage = res.data?.meta?.last_page ?? revPage;
          if (revPage >= lastPage) break;
          revPage++;
        }
      }

      const payload = {
        data: reviews,
        meta: { total: reviews.length, properties: properties.length },
      };
      return { content: [{ type: "text", text: formatResponse(payload) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_respond_to_review", {
    title: "Respond to Review",
    description: "Post a public response to a guest review.",
    inputSchema: {
      reviewId: z.string().describe("Review UUID"),
      body: z.string().describe("Your public response text"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
  }, async ({ reviewId, body }) => {
    try {
      const res = await getHospitable().post(`/reviews/${reviewId}/response`, { body });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
