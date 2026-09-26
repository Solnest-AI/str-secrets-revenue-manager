import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getPriceLabs } from "../src/services/pricelabs-client.js";
import { registerOverrideTools } from "../src/tools/overrides.js";

test("set_overrides always sends update_children: false", async (t) => {
  process.env.PRICELABS_API_KEY = "test-only";
  const http = getPriceLabs();
  const previous = http.defaults.adapter;
  const sent: unknown[] = [];
  http.defaults.adapter = async (config) => {
    sent.push(JSON.parse(config.data));
    return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
  };
  const server = new McpServer({ name: "test", version: "1.0.0" });
  registerOverrideTools(server);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => {
    http.defaults.adapter = previous;
    await client.close();
    await server.close();
  });
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  const result = await client.callTool({
    name: "pricelabs_set_overrides",
    arguments: { listing_id: "L1", pms: "smartbnb", overrides: [{ date: "2026-12-01", price: 200, price_type: "fixed" }] },
  });
  assert.ok(!result.isError);
  assert.equal(sent.length, 1);
  assert.equal((sent[0] as { update_children: unknown }).update_children, false);
});
