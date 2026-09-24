# UPDATE — Solnest Revenue Manager (Claude reads this)

**You are Claude. The user pasted this repo's link and asked you to update their Solnest Revenue Manager. Do it FOR them, safely, in plain language.**

**The one rule that matters: this update must NOT wipe their API keys.** Their PriceLabs, Hospitable, Turno, AirROI, and RankBreeze credentials live in `.env` files *inside* the MCP server folders. Overwrite those and their whole pricing setup goes dark — and they'd have to go re-fetch five separate keys. Follow the preserve list below exactly.

---

## Step 0 — Find their install

This tool has two halves. Find both.

**The plugin** (the skill Claude uses):
- macOS / Linux: `~/.claude/plugins/revenue-manager-plugin/`
- Windows: `%USERPROFILE%\.claude\plugins\revenue-manager-plugin\`

**The MCP servers** (the things that talk to PriceLabs etc.). These live wherever the user unzipped the bundle. Find them by reading their MCP config — `~/.claude.json` — and looking for the server entries named `pricelabs`, `hospitable`, `turno`, `airroi`, or `rankbreeze`. The paths in there point at the real folder.

If you can't find either half, they never installed it. Tell them to grab the zip from Skool and drag it in for a fresh install, and stop here.

## Step 1 — Check if they even need this

Compare `VERSION` in their install to `VERSION` in this repo. If they match, say so and stop:

> You're already on the latest version (`X.Y.Z`) — nothing to update.

## Step 2 — PRESERVE THIS (do not skip)

Back these up to a temp folder before touching anything. These are **theirs**:

| Path | What it is |
|---|---|
| `mcp-servers/*/.env` | **Their API keys.** All five. Irreplaceable without re-fetching each one. |
| `mcp-servers/rankbreeze/session.txt` | Their RankBreeze session cookie. |
| Any `.venv/` or `node_modules/` in the server folders | Installed dependencies. Keep them so they don't have to reinstall. |
| Their `~/.claude.json` MCP entries | The registration. **Do not rewrite this file.** |

The `.env.example` files SHOULD be overwritten — those are empty templates, not their keys.

**Their property config (markup %, floors, ceilings) lives in Supabase, not on disk.** The update never touches it. Do not re-run the property-config wizard and do not re-run any migration that would reset it.

## Step 3 — Pull the latest

```bash
git clone --depth 1 https://github.com/Solnest-AI/solnest-revenue-manager.git /tmp/srm-update
```

No `git`? Download `https://github.com/Solnest-AI/solnest-revenue-manager/archive/refs/heads/main.zip` instead.

## Step 4 — Copy the new files over

Copy file over file. **Never delete a folder wholesale** — that's what kills the `.env` files.

Update these:
- `revenue-manager-plugin/` → their `~/.claude/plugins/revenue-manager-plugin/`
- `mcp-servers/*/` — the **code only** (`.py`, `.json`, `.md`, `.txt` requirements). Skip every `.env` and `session.txt`.
- `README.md`, `SETUP.md`, `CHANGELOG.md`, `VERSION`

If `requirements.txt` changed for any MCP server, re-run the install for that server's environment so new dependencies land. Tell the user that's what you're doing — it can take a minute.

## Step 5 — Put their keys back

Restore every `.env` and `session.txt` you backed up in Step 2. Then verify, before you say anything to the user:

- Does each of the five `mcp-servers/*/.env` still exist?
- Is each one still **non-empty**?

If any came back empty or missing, restore it from the backup. **Never report success until you've confirmed their keys survived.**

## Step 6 — Restart and confirm

Tell them to **fully quit and reopen Claude Code** so the plugin and MCP servers reload. Then have them say "check my pricing" — if it answers, the update took.

Summarize what changed from `CHANGELOG.md` — two or three plain bullets, new version only.

> Updated to `X.Y.Z`. All five of your API connections came through untouched. Quit and reopen Claude Code, then say "check my pricing" and you're back in business.

## If something goes wrong

Restore the Step 2 backup. One friendly sentence — never a raw error. Their keys are the only thing that's genuinely painful to replace, and you have a backup of them.
