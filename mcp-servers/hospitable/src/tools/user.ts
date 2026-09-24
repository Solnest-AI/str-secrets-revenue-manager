import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getHospitable, formatResponse, handleError } from "../services/hospitable-client.js";

export function registerUserTools(server: McpServer): void {

  server.registerTool("hospitable_get_user", {
    title: "Get User Profile",
    description: "Get the authenticated user's profile and account information.",
    inputSchema: {},
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
  }, async () => {
    try {
      const res = await getHospitable().get("/user");
      return { content: [{ type: "text", text: formatResponse(res.data) }] };
    } catch (e) { return { content: [{ type: "text", text: handleError(e) }] }; }
  });
}
