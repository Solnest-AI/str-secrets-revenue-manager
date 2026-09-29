import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getPriceLabs } from "../src/services/pricelabs-client.js";
import { registerListingTools } from "../src/tools/listings.js";
import { registerOverrideTools } from "../src/tools/overrides.js";
import { listingUpdateProblems, overrideLiveProblems, overrideProblems } from "../src/tools/guards.js";

const LIVE = { min: 150, base: 200, max: 400, currency: "CAD" };

test("a price needs its type: 250 alone could be dollars or +250%", () => {
  assert.match(overrideProblems([{ date: "2026-12-01", price: 250 }])[0], /needs price_type/);
});

test("a percent outside -75..500 is refused, pointing at 'fixed' for dollars", () => {
  assert.match(overrideProblems([{ date: "2026-12-01", price: 600, price_type: "percent" }])[0], /percent range/);
  assert.deepEqual(overrideProblems([{ date: "2026-12-01", price: 25, price_type: "percent" }]), []);
});

test("a fixed price needs a currency and must be above zero", () => {
  assert.match(overrideProblems([{ date: "2026-12-01", price: 200, price_type: "fixed" }])[0], /currency/);
  assert.match(overrideProblems([{ date: "2026-12-01", price: 0, price_type: "fixed", currency: "CAD" }])[0], /above zero/);
});

test("bad dates, duplicates, min stay and day masks are refused", () => {
  const p = overrideProblems([
    { date: "2026-02-30", min_stay: 2 },
    { date: "2026-12-01", min_stay: 0 },
    { date: "2026-12-01", check_in: "1111" },
  ]);
  assert.equal(p.length, 4, p.join("\n"));
});

test("live: wrong currency, below min, and a price in cents are refused", () => {
  const ok = { date: "2026-12-01", price: 220, price_type: "fixed" as const, currency: "CAD" };
  assert.deepEqual(overrideLiveProblems([ok], LIVE), []);
  assert.match(overrideLiveProblems([{ ...ok, currency: "USD" }], LIVE)[0], /priced in CAD/);
  assert.match(overrideLiveProblems([{ ...ok, price: 140 }], LIVE)[0], /below the listing min/);
  assert.match(overrideLiveProblems([{ ...ok, price: 22000 }], LIVE)[0], /cents/);
});

test("listing update: order, zero and 5x jumps are refused", () => {
  assert.deepEqual(listingUpdateProblems({ id: "L1", pms: "smartbnb", min: 160 }, LIVE), []);
  assert.match(listingUpdateProblems({ id: "L1", pms: "smartbnb", min: 450 }, LIVE).join(), /min <= base <= max/);
  assert.match(listingUpdateProblems({ id: "L1", pms: "smartbnb", min: 0 }, LIVE).join(), /above zero/);
  assert.match(listingUpdateProblems({ id: "L1", pms: "smartbnb", base: 20000, max: 40000 }, LIVE).join(), /5x/);
});

async function connect(t: test.TestContext, register: (s: McpServer) => void) {
  process.env.PRICELABS_API_KEY = "test-only";
  const http = getPriceLabs();
  const previous = http.defaults.adapter;
  const writes: unknown[] = [];
  http.defaults.adapter = async (config) => {
    if (config.method === "get") {
      return { data: { listings: [{ id: "L1", pms: "smartbnb", ...LIVE }] }, status: 200, statusText: "OK", headers: {}, config };
    }
    writes.push(config);
    return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
  };
  const server = new McpServer({ name: "test", version: "1.0.0" });
  register(server);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [c, s] = InMemoryTransport.createLinkedPair();
  t.after(async () => {
    http.defaults.adapter = previous;
    await client.close();
    await server.close();
  });
  await Promise.all([server.connect(s), client.connect(c)]);
  return { client, writes };
}

test("set_overrides: without confirm, or with a guard problem, nothing is sent", async (t) => {
  const { client, writes } = await connect(t, registerOverrideTools);
  const base = { listing_id: "L1", pms: "smartbnb" };
  const noConfirm = await client.callTool({ name: "pricelabs_set_overrides", arguments: {
    ...base, overrides: [{ date: "2026-12-01", price: 220, price_type: "fixed", currency: "CAD" }], confirm: false } });
  const belowMin = await client.callTool({ name: "pricelabs_set_overrides", arguments: {
    ...base, overrides: [{ date: "2026-12-01", price: 120, price_type: "fixed", currency: "CAD" }], confirm: true } });
  assert.ok(noConfirm.isError && belowMin.isError);
  assert.equal(writes.length, 0);
});

test("update_listings: a min above the max is refused before any POST", async (t) => {
  const { client, writes } = await connect(t, registerListingTools);
  const r = await client.callTool({ name: "pricelabs_update_listings", arguments: {
    listings: [{ id: "L1", pms: "smartbnb", min: 500 }], confirm: true } });
  assert.ok(r.isError);
  assert.equal(writes.length, 0);
  const ok = await client.callTool({ name: "pricelabs_update_listings", arguments: {
    listings: [{ id: "L1", pms: "smartbnb", min: 160 }], confirm: true } });
  assert.ok(!ok.isError);
  assert.equal(writes.length, 1);
});
