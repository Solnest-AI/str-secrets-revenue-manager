import { config } from "dotenv";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: resolve(__dirname, "..", ".env"), quiet: true });

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { registerListingTools } from "./tools/listings.js";
import { registerPricingTools } from "./tools/pricing.js";
import { registerOverrideTools } from "./tools/overrides.js";
import { registerMarketTools } from "./tools/market.js";
import { registerReservationTools } from "./tools/reservations.js";

async function main(): Promise<void> {
  if (!process.env.PRICELABS_API_KEY) {
    console.error("ERROR: PRICELABS_API_KEY environment variable is required");
    process.exit(1);
  }

  const server = new McpServer({
    name: "pricelabs-mcp-server",
    version: "1.0.0",
  });

  // Register all tool groups
  registerListingTools(server);
  registerPricingTools(server);
  registerOverrideTools(server);
  registerMarketTools(server);
  registerReservationTools(server);

  // Connect via stdio transport
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
