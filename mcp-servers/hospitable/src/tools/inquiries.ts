import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerInquiryTools(server: McpServer): void {

  server.registerTool("hospitable_list_inquiries", {
    title: "List Inquiries",
    description: "List guest inquiries — pre-booking questions and requests.",
    inputSchema: {
      property_id: z.string().optional().describe("Filter by property UUID"),
      page: z.number().int().min(1).optional().describe("Page number"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ property_id, page, per_page }) => {
    try {
      const params: Record<string, unknown> = {};
      if (property_id) params.property_id = property_id;
      if (page) params.page = page;
      if (per_page) params.per_page = per_page;
      const res = await getHospitable().get("/inquiries", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_inquiry", {
    title: "Get Inquiry",
    description: "Get a single inquiry by UUID — full guest question details.",
    inputSchema: {
      inquiryId: z.string().describe("Inquiry UUID"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ inquiryId }) => {
    try {
      const res = await getHospitable().get(`/inquiries/${inquiryId}`);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
