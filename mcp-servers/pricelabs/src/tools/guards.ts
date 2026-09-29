// Price guards for the raw PriceLabs write tools. The Revenue Manager never calls these tools
// (it writes through fetch/apply_change.py), so this is the backstop for a direct call: a
// percent read as dollars, a price in cents, a missing or wrong currency, or a min above the max
// is refused before anything reaches PriceLabs.
import { AxiosInstance } from "axios";

export const PERCENT_MIN = -75;
export const PERCENT_MAX = 500;
// A new min/base/max more than this factor away from the live value is almost always a unit
// mistake (cents for dollars) or a typo, not a pricing decision.
export const MAX_LISTING_FACTOR = 5;
// A fixed night price above this multiple of the listing max is refused for the same reason.
export const MAX_NIGHT_OVER_MAX = 3;

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DOW = /^[01]{7}$/;

export interface LiveListing { min: number; base: number; max: number; currency: string | null }

export interface Override {
  date: string;
  price?: number; price_type?: "fixed" | "percent"; currency?: string;
  min_stay?: number;
  min_price?: number; min_price_type?: "fixed" | "percent";
  max_price?: number; max_price_type?: "fixed" | "percent";
  base_price?: number;
  check_in?: string; check_out?: string;
}

export interface ListingUpdate { id: string; pms: string; min?: number; base?: number; max?: number }

function realDate(d: string): boolean {
  if (!DATE.test(d)) return false;
  const t = new Date(d + "T00:00:00Z");
  return !Number.isNaN(t.getTime()) && t.toISOString().slice(0, 10) === d;
}

function money(v: number): string {
  return v.toFixed(2);
}

/** One amount with its type: a fixed amount is a positive price, a percent stays in PriceLabs' range. */
function amountProblem(d: string, field: string, value: number | undefined, type: string | undefined,
                       currency: string | undefined): string | null {
  if (value === undefined) return null;
  if (!Number.isFinite(value)) return `${d}: ${field} is not a number`;
  if (!type) return `${d}: ${field} needs ${field === "price" ? "price_type" : field + "_type"} ('fixed' or 'percent'); ${value} alone could be dollars or a percent`;
  if (type === "percent") {
    if (value < PERCENT_MIN || value > PERCENT_MAX) {
      return `${d}: ${field} ${value}% is outside PriceLabs' ${PERCENT_MIN} to ${PERCENT_MAX} percent range (a dollar amount needs type 'fixed')`;
    }
    return null;
  }
  if (value <= 0) return `${d}: a fixed ${field} must be above zero`;
  if (!currency) return `${d}: a fixed ${field} needs the listing's currency (e.g. CAD, USD)`;
  return null;
}

/** Shape checks that need no network. Returns every problem found, empty when clean. */
export function overrideProblems(overrides: Override[]): string[] {
  const out: string[] = [];
  if (overrides.length === 0) out.push("no overrides given");
  const seen = new Set<string>();
  for (const o of overrides) {
    const d = o.date;
    if (!realDate(d)) { out.push(`${d}: not a real YYYY-MM-DD date`); continue; }
    if (seen.has(d)) out.push(`${d}: appears twice`);
    seen.add(d);
    for (const p of [
      amountProblem(d, "price", o.price, o.price_type, o.currency),
      amountProblem(d, "min_price", o.min_price, o.min_price_type, o.currency),
      amountProblem(d, "max_price", o.max_price, o.max_price_type, o.currency),
    ]) if (p) out.push(p);
    if (o.base_price !== undefined && !(o.base_price > 0)) out.push(`${d}: base_price must be above zero`);
    if (o.base_price !== undefined && !o.currency) out.push(`${d}: base_price needs the listing's currency`);
    if (o.min_stay !== undefined && (o.min_stay < 1 || o.min_stay > 365)) out.push(`${d}: min_stay must be 1 to 365 nights`);
    for (const [k, v] of [["check_in", o.check_in], ["check_out", o.check_out]] as const) {
      if (v !== undefined && !DOW.test(v)) out.push(`${d}: ${k} must be 7 characters of 0/1, Monday first`);
    }
    if (o.min_price_type === "fixed" && o.max_price_type === "fixed" && o.min_price !== undefined
        && o.max_price !== undefined && o.min_price > o.max_price) {
      out.push(`${d}: min_price ${o.min_price} is above max_price ${o.max_price}`);
    }
  }
  return out;
}

/** Checks against the live listing: currency matches, no fixed night under the min or absurdly over the max. */
export function overrideLiveProblems(overrides: Override[], live: LiveListing): string[] {
  const out: string[] = [];
  for (const o of overrides) {
    const d = o.date;
    const fixed = o.price_type === "fixed" || o.min_price_type === "fixed" || o.max_price_type === "fixed"
      || o.base_price !== undefined;
    if (fixed && o.currency && live.currency && o.currency.toUpperCase() !== live.currency.toUpperCase()) {
      out.push(`${d}: currency ${o.currency} but the listing is priced in ${live.currency}`);
    }
    if (o.price_type === "fixed" && o.price !== undefined) {
      if (o.price < live.min) {
        out.push(`${d}: ${money(o.price)} is below the listing min of ${money(live.min)}`);
      } else if (o.price > live.max * MAX_NIGHT_OVER_MAX) {
        out.push(`${d}: ${money(o.price)} is more than ${MAX_NIGHT_OVER_MAX}x the listing max of ${money(live.max)} (a price in cents?)`);
      }
    }
  }
  return out;
}

/** min <= base <= max after the update, all positive, and no value more than 5x away from live. */
export function listingUpdateProblems(u: ListingUpdate, live: LiveListing): string[] {
  const out: string[] = [];
  const merged = { min: u.min ?? live.min, base: u.base ?? live.base, max: u.max ?? live.max };
  for (const f of ["min", "base", "max"] as const) {
    const v = u[f];
    if (v === undefined) continue;
    if (!Number.isFinite(v) || v <= 0) { out.push(`${u.id}: ${f} must be above zero`); continue; }
    const now = live[f];
    if (now > 0 && (v > now * MAX_LISTING_FACTOR || v < now / MAX_LISTING_FACTOR)) {
      out.push(`${u.id}: ${f} ${money(v)} is more than ${MAX_LISTING_FACTOR}x away from the live ${money(now)} (a price in cents, or a typo?)`);
    }
  }
  if (!(merged.min <= merged.base && merged.base <= merged.max)) {
    out.push(`${u.id}: min <= base <= max would not hold (min ${money(merged.min)}, base ${money(merged.base)}, max ${money(merged.max)})`);
  }
  return out;
}

/** The live min/base/max/currency for one listing, or a thrown Error naming why it could not be read. */
export async function readLiveListing(http: AxiosInstance, id: string, pms: string): Promise<LiveListing> {
  const res = await http.get(`/v1/listings/${encodeURIComponent(id)}`, { params: { pms } });
  const rows = (res.data as { listings?: unknown })?.listings;
  const item = Array.isArray(rows) && rows.length === 1 ? rows[0] as Record<string, unknown> : null;
  if (!item || String(item.id) !== id) throw new Error(`PriceLabs did not return listing ${id} (pms ${pms})`);
  const num = (k: string) => {
    const v = Number(item[k]);
    if (!Number.isFinite(v) || v <= 0) throw new Error(`listing ${id} has no usable live ${k}`);
    return v;
  };
  return { min: num("min"), base: num("base"), max: num("max"),
           currency: typeof item.currency === "string" && item.currency ? item.currency : null };
}

export function refusal(tool: string, problems: string[]): { isError: true; content: { type: "text"; text: string }[] } {
  return { isError: true, content: [{ type: "text", text:
    `Refused: ${tool} sent nothing.\n- ${problems.join("\n- ")}\n` +
    "Revenue changes should go through the Revenue Manager's safe writer (fetch/apply_change.py), " +
    "which shows the plan, saves an undo and re-reads the result." }] };
}

export const CONFIRM_TEXT = "Must be true. Pass it only after the operator has seen the exact listing, dates and " +
  "amounts (with currency) and said yes; the tool refuses otherwise.";
