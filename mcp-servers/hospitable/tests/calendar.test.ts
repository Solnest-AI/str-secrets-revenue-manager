import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { AxiosAdapter } from "axios";
import { getHospitable } from "../src/services/hospitable-client.js";
import { registerPropertyTools } from "../src/tools/properties.js";

type Put = { method?: string; body: unknown };

function fakeApi(currentCents: number | null, currency = "CAD") {
  const puts: Put[] = [];
  const adapter: AxiosAdapter = async (config) => {
    if (config.method === "get") {
      const days = currentCents === null ? [] : [
        { date: "2026-12-01", price: { amount: currentCents, currency } },
        { date: "2026-12-02", price: { amount: currentCents, currency } },
      ];
      return { data: { data: { days } }, status: 200, statusText: "OK", headers: {}, config };
    }
    puts.push({ method: config.method, body: JSON.parse(config.data) });
    return { data: { status: "accepted" }, status: 202, statusText: "Accepted", headers: {}, config };
  };
  return { adapter, puts };
}

async function connect(t: TestContext, adapter: AxiosAdapter) {
  process.env.HOSPITABLE_API_KEY = "test-only";
  const http = getHospitable();
  const previous = http.defaults.adapter;
  http.defaults.adapter = adapter;
  const server = new McpServer({ name: "test", version: "1.0.0" });
  registerPropertyTools(server);
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

const call = (client: Client, dates: unknown[]) =>
  client.callTool({ name: "hospitable_update_property_calendar", arguments: { propertyId: "p1", dates } });

test("dollar price is sent as documented price.amount in cents, minimum_stay as min_stay", async (t) => {
  const api = fakeApi(28500);
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", price: 299.99, minimum_stay: 3 }]);
  assert.ok(!result.isError, JSON.stringify(result.content));
  assert.deepEqual(api.puts[0].body, { dates: [{ date: "2026-12-01", min_stay: 3, price: { amount: 29999 } }] });
});

test("a cents value passed as dollars is refused and nothing is written", async (t) => {
  const api = fakeApi(28500);
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", price: 250 }, { date: "2026-12-02", price: 28500 }]);
  assert.equal(result.isError, true);
  assert.match((result.content as Array<{ text: string }>)[0].text, /major units/);
  assert.equal(api.puts.length, 0);
});

test("a price far below current is refused", async (t) => {
  const api = fakeApi(28500);
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", price: 2.85 }]);
  assert.equal(result.isError, true);
  assert.equal(api.puts.length, 0);
});

test("no readable current price means no write", async (t) => {
  const api = fakeApi(null);
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", price: 285 }]);
  assert.equal(result.isError, true);
  assert.equal(api.puts.length, 0);
});

test("zero-decimal currencies are not multiplied by 100", async (t) => {
  const api = fakeApi(30000, "JPY");
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", price: 32000 }]);
  assert.ok(!result.isError, JSON.stringify(result.content));
  assert.deepEqual(api.puts[0].body, { dates: [{ date: "2026-12-01", price: { amount: 32000 } }] });
});

test("availability-only writes skip the price read", async (t) => {
  const api = fakeApi(null);
  const client = await connect(t, api.adapter);
  const result = await call(client, [{ date: "2026-12-01", available: false }]);
  assert.ok(!result.isError, JSON.stringify(result.content));
  assert.deepEqual(api.puts[0].body, { dates: [{ date: "2026-12-01", available: false }] });
});
