import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { AxiosAdapter } from "axios";
import { getHospitable } from "../src/services/hospitable-client.js";
import { registerReservationTools } from "../src/tools/reservations.js";

async function connect(t: TestContext, adapter: AxiosAdapter) {
  process.env.HOSPITABLE_API_KEY = "test-only";
  const http = getHospitable();
  const previous = http.defaults.adapter;
  http.defaults.adapter = adapter;
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

const echo: AxiosAdapter = async (config) => ({
  data: { params: config.params }, status: 200, statusText: "OK", headers: {}, config,
});

function payload(result: Awaited<ReturnType<Client["callTool"]>>) {
  assert.ok(!result.isError, JSON.stringify(result.content));
  const content = result.content as Array<{ type: string; text: string }>;
  return JSON.parse(content[0].text);
}

test("historical date range and financial includes reach the API", async (t) => {
  const client = await connect(t, echo);
  const result = payload(await client.callTool({
    name: "hospitable_list_reservations",
    arguments: { properties: ["property-1"], start_date: "2025-01-01", end_date: "2025-01-31", include: "financials" },
  }));
  assert.equal(result.params.start_date, "2025-01-01");
  assert.equal(result.params.end_date, "2025-01-31");
  assert.equal(result.params.include, "financials");
});

test("legacy check-in filters map to supported date parameters", async (t) => {
  const client = await connect(t, echo);
  const result = payload(await client.callTool({
    name: "hospitable_list_reservations",
    arguments: { property_id: "property-1", check_in_from: "2025-01-01", check_in_to: "2025-01-31" },
  }));
  assert.equal(result.params.start_date, "2025-01-01");
  assert.equal(result.params.end_date, "2025-01-31");
  assert.equal(result.params.check_in_from, undefined);
});

test("unsupported historical filters fail instead of returning an unfiltered success", async (t) => {
  const client = await connect(t, echo);
  const result = await client.callTool({
    name: "hospitable_list_reservations",
    arguments: { property_id: "property-1", created_from: "2025-01-01" },
  });
  assert.equal(result.isError, true);
});

test("single reservation reads can request financial details", async (t) => {
  const client = await connect(t, echo);
  const result = payload(await client.callTool({
    name: "hospitable_get_reservation",
    arguments: { reservationId: "reservation-1", include: "financials" },
  }));
  assert.equal(result.params?.include, "financials");
});

test("automatic discovery includes reservations on the second property page", async (t) => {
  const client = await connect(t, async (config) => {
    let data;
    if (config.url === "/properties") {
      data = config.params?.page === 2
        ? { data: [{ id: "property-101" }], meta: { current_page: 2, last_page: 2 } }
        : { data: Array.from({ length: 100 }, (_, index) => ({ id: `property-${index + 1}` })), meta: { current_page: 1, last_page: 2 } };
    } else {
      data = { data: config.params["properties[]"].includes("property-101") ? [{ id: "reservation-101" }] : [] };
    }
    return { data, status: 200, statusText: "OK", headers: {}, config };
  });
  const result = payload(await client.callTool({ name: "hospitable_list_reservations", arguments: {} }));
  assert.deepEqual(result.data, [{ id: "reservation-101" }]);
});

test("API failures are marked as MCP tool errors", async (t) => {
  const client = await connect(t, async () => { throw new Error("simulated API outage"); });
  const result = await client.callTool({
    name: "hospitable_list_reservations", arguments: { properties: ["property-1"] },
  });
  assert.equal(result.isError, true);
});
