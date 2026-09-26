# The spreadsheet deliverable (Step 7.5)

Part of the revenue-manager skill. SKILL.md says when to read this file.

Only build it if the operator said yes to the offer. It is **pure output**: it reads nothing
new, pushes nothing, and writes nothing to Supabase. It reflects the current state:
recommendations are marked `proposed`, or `applied` if the operator already approved and the
writer applied them in Step 8.

**1. Assemble the data as JSON.** You already have everything from Steps 4 to 7. Build one
JSON object matching the shape documented at the top of `report/build_workbook.py` (use
`report/sample_data.json` as the working template): a `meta` block, a `portfolio_summary`
roll-up, and a `properties[]` array where each property carries pricing (current vs
recommended base/min/max), comps, ask-vs-cleared, KPIs, red flags, the recommendations table,
any DSO/min-stay recs, and the safety-layer footer. Write it to a temp file **in the OS temp
dir** (e.g. `/tmp/rm_report.json`), never inside the bundle.

**2. Generate**, from this skill's folder (uv fetches openpyxl for this one run; nothing is
installed into the bundle):
```bash
uv run --python 3.13 --with openpyxl python report/build_workbook.py /tmp/rm_report.json
```
If that fails (no network, no uv), run the same script without `--with openpyxl`: it
auto-degrades to a folder of CSVs (one summary + one per property) so the operator still gets
every number. With no output path the workbook lands on the operator's Desktop (or the current
folder) as `Revenue-Report-<YYYY-MM-DD>.xlsx`.

**3. Report the path.** The script prints exactly one result line: `WORKBOOK_WRITTEN: <path>`
(real xlsx) or `CSV_FALLBACK_WRITTEN: <dir>/` (openpyxl unavailable). Tell the operator the
exact path and which format they got.
