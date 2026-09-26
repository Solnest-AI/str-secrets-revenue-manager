import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getPriceLabs } from "../src/services/pricelabs-client.js";
import { registerReservationTools } from "../src/tools/reservations.js";

async function connect(t: TestContext, response?: unknown) {
  process.env.PRICELABS_API_KEY = "test-only";
  const http = getPriceLabs();
  const previous = http.defaults.adapter;
  http.defaults.adapter = async (config) => ({
    data: response ?? { params: config.params }, status: 200, statusText: "OK", headers: {}, config,
  });
  const server = new McpServer({ name: "test", version: "1.0.0" });
  registerReservationTools(server);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  t.after(async () => {
    http.defaults.adapter = previous;
    await client.close();
    await server.close();
  });
  return client;
}

function payload(result: Awaited<ReturnType<Client["callTool"]>>) {
  assert.ok(!result.isError, JSON.stringify(result.content));
  return JSON.parse((result.content as Array<{ text: string }>)[0].text);
}

for (const args of [{ start_date: "2025-01-01", end_date: "2025-02-01" }, { pms: "smartbnb" }, { pms: "smartbnb", start_date: "2025-01-01" }]) {
  test(`incomplete required reservation filters are rejected: ${JSON.stringify(args)}`, async (t) => {
    const client = await connect(t);
    const result = await client.callTool({ name: "pricelabs_list_reservations", arguments: args });
    assert.equal(result.isError, true);
  });
}

test("booking-date filters and pagination reach the API", async (t) => {
  const client = await connect(t);
  const result = payload(await client.callTool({
    name: "pricelabs_list_reservations",
    arguments: { pms: "smartbnb", booked_start_date: "2025-01-01", offset: 500, limit: 500 },
  }));
  assert.equal(result.params.booked_start_date, "2025-01-01");
  assert.equal(result.params.offset, 500);
});

test("single-property reservations exclude other listings even when the API ignores listing_id", async (t) => {
  const client = await connect(t, {
    pms_name: "smartbnb", next_page: true,
    data: [{ listing_id: "boho", reservation_id: "wanted" }, { listing_id: "other", reservation_id: "unrelated" }],
  });
  const result = payload(await client.callTool({
    name: "pricelabs_list_reservations",
    arguments: { pms: "smartbnb", listing_id: "boho", start_date: "2025-01-01", end_date: "2025-02-01" },
  }));
  assert.deepEqual(result.data, [{ listing_id: "boho", reservation_id: "wanted" }]);
  assert.equal(result.next_page, true);
});

test("an empty filtered page preserves next_page so later matching reservations are not lost", async (t) => {
  const client = await connect(t, { pms_name: "smartbnb", next_page: true, data: [{ listing_id: "other" }] });
  const result = payload(await client.callTool({
    name: "pricelabs_list_reservations",
    arguments: { pms: "smartbnb", listing_id: "boho", start_date: "2025-01-01", end_date: "2025-02-01" },
  }));
  assert.deepEqual(result.data, []);
  assert.equal(result.next_page, true);
});
