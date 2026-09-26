import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getPriceLabs } from "../src/services/pricelabs-client.js";
import { registerListingTools } from "../src/tools/listings.js";

test("API failures are marked as MCP tool errors", async (t) => {
  process.env.PRICELABS_API_KEY = "test-only";
  const http = getPriceLabs();
  const previous = http.defaults.adapter;
  http.defaults.adapter = async () => { throw new Error("simulated API outage"); };
  const server = new McpServer({ name: "test", version: "1.0.0" });
  registerListingTools(server);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => {
    http.defaults.adapter = previous;
    await client.close();
    await server.close();
  });
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  const result = await client.callTool({ name: "pricelabs_list_listings", arguments: {} });
  assert.equal(result.isError, true);
});
