import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerTransactionTools(server: McpServer): void {

  server.registerTool("hospitable_list_transactions", {
    title: "List Transactions",
    description: "List payouts and transactions. Filter by property, date range, or type.",
    inputSchema: {
      property_id: z.string().optional().describe("Filter by property UUID"),
      from: z.string().optional().describe("Start date (YYYY-MM-DD)"),
      to: z.string().optional().describe("End date (YYYY-MM-DD)"),
      page: z.number().int().min(1).optional().describe("Page number"),
      per_page: z.number().int().min(1).max(100).optional().describe("Results per page"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ property_id, from, to, page, per_page }) => {
    try {
      const params: Record<string, unknown> = {};
      if (property_id) params.property_id = property_id;
      if (from) params.from = from;
      if (to) params.to = to;
      if (page) params.page = page;
      if (per_page) params.per_page = per_page;
      const res = await getHospitable().get("/transactions", { params });
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });

  server.registerTool("hospitable_get_transaction", {
    title: "Get Transaction",
    description: "Get a single transaction/payout by UUID — full financial details.",
    inputSchema: {
      transactionId: z.string().describe("Transaction UUID"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async ({ transactionId }) => {
    try {
      const res = await getHospitable().get(`/transactions/${transactionId}`);
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
