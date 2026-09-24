import { config } from "dotenv";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: resolve(__dirname, "..", ".env"), quiet: true });

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { registerPropertyTools } from "./tools/properties.js";
import { registerReservationTools } from "./tools/reservations.js";
import { registerMessageTools } from "./tools/messages.js";
import { registerReviewTools } from "./tools/reviews.js";
import { registerTransactionTools } from "./tools/transactions.js";
import { registerInquiryTools } from "./tools/inquiries.js";
import { registerUserTools } from "./tools/user.js";

async function main(): Promise<void> {
  if (!process.env.HOSPITABLE_API_KEY) {
    console.error("ERROR: HOSPITABLE_API_KEY environment variable is required");
    process.exit(1);
  }

  const server = new McpServer({
    name: "hospitable-mcp-server",
    version: "1.0.0",
  });

  // Register all tool groups
  registerPropertyTools(server);
  registerReservationTools(server);
  registerMessageTools(server);
  registerReviewTools(server);
  registerTransactionTools(server);
  registerInquiryTools(server);
  registerUserTools(server);

  // Connect via stdio transport
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
