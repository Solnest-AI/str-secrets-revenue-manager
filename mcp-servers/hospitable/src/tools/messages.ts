import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerMessageTools(server: McpServer): void {

  server.registerTool("hospitable_list_messages", {
    title: "List Messages",
    description: "List messages for a reservation — the full conversation thread between host and guest.",
    inputSchema: {
      reservationId: z.string().describe("Reservation UUID"),
      page: z.number().int().min(1).optional().describe("Page number"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ reservationId, page, per_page }) => {
    try {
      const params: Record<string, unknown> = {};
      if (page) params.page = page;
      if (per_page) params.per_page = per_page;
      const res = await getHospitable().get(`/reservations/${reservationId}/messages`, { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_send_message", {
    title: "Send Message",
    description: "Send a message to a guest for a specific reservation.",
    inputSchema: {
      reservationId: z.string().describe("Reservation UUID"),
      body: z.string().describe("Message body text"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
  }, async ({ reservationId, body }) => {
    try {
      const res = await getHospitable().post(`/reservations/${reservationId}/messages`, { body });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
