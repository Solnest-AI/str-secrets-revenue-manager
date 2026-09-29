# STR Secrets AI Revenue Manager

Your AI revenue manager, running inside Claude Code. It reads your real PMS calendar and
your pricing tool, checks the whole booking picture first, and tells you exactly what to
change. It tells you what your min price should be too. You don't have to guess it.

And nothing changes on its own. It shows you every change on a card and asks. You say yes,
it applies it, then it reads it back to prove it took. You say no, nothing happens.

## Before you start

You did the **STR Secrets connections kit** the week before the summit. That is what
connected your PMS, your pricing tool and your database and put your keys in the right place.
Keep that folder where you unzipped it: this setup goes and finds it, re-checks every
connection, and installs anything that is missing before it touches the Revenue Manager.

## Setup

Paste this link into Claude Code and say **Set this up**:

https://github.com/Solnest-AI/str-secrets-revenue-manager

Claude downloads it to a folder called `str-secrets-revenue-manager` in your home folder.
(Already have the folder? Open Claude Code in it and say **Set up the revenue manager.**)

Claude follows `SETUP.md`, in this order:

1. Checks the tools (Git, Node, uv, Python) and installs any that are missing.
2. Finds your connections kit, re-checks your PMS, pricing tool and Supabase rows, and repairs
   what it can.
3. Installs the plugin.
4. Asks the markup you add on each booking site (it reads which sites you're on from your
   PMS) and maps each property to your pricing tool.
5. Shows a green check ✅, then offers to run it on one of your properties right away. No
   restart needed.

> **Claude:** if someone pasted this repo's link and said "set this up", clone it to
> `~/str-secrets-revenue-manager` (if that folder already exists, `git pull` there instead).
> If Claude Code is already open inside this repo, skip the clone and use this folder. Then
> follow `SETUP.md`, starting every command with `cd ~/str-secrets-revenue-manager &&` (or
> this folder), because Claude Code can reset the working folder between commands. When you
> tell them to quit and reopen, give them the folder's full path to reopen in.
>
> **On a Mac, if `git` fails with `xcrun: error` or `invalid active developer path`,** Git is not
> installed yet (it comes with Apple's command line tools; step 0 opens Apple's installer for
> them). Do not stop to install it: nothing in this setup needs Git once the folder is here. Get
> the same folder without it, and run this same line again to update a folder that has no `.git`:
>
> ```bash
> D=~/str-secrets-revenue-manager; mkdir -p "$D" && curl -fsSL -o "$D.tar.gz" https://github.com/Solnest-AI/str-secrets-revenue-manager/archive/refs/heads/main.tar.gz && tar -xzf "$D.tar.gz" -C "$D" --strip-components 1 && rm -f "$D.tar.gz"
> ```

## Using it

Say **"check my pricing"**, or ask about one property by name. Every property card opens
with the Revenue Flywheel, in order:

**Visibility → Bookings → Reviews → Ranking**

If a piece is missing (say you don't use a ranking tool), it still prices the property. It
just tells you up top what it priced without. The only thing that stops it is no PMS
calendar, because then there are no nights to price.

Every recommendation goes through the safety layer: your min and max, a flag on anything
that moves a price more than 15%, how many comps it's based on, your currency, and how
fresh the data is. It also checks whether each of your PriceLabs rules (last-minute,
far-out, day-of-week, seasonality) is actually helping, against the market.

## What's tested, straight up

All 8 PMSs and both pricing tools run through the same runner and the same safe writer.
"Live-tested" means we ran it against a real account. "Built from docs" means it was built
from that company's own API docs and tested against fakes, but hasn't touched a real account
yet, so check the numbers on your first run.

| Tool | Reading your data | Changing a price |
|---|---|---|
| Hospitable | Live-tested | Live-tested (write, verify, undo) |
| Guesty | Live-tested | Built from docs |
| OwnerRez | Live-tested | Built from docs |
| Hostaway | Built from docs | Built from docs |
| Lodgify | Built from docs | Built from docs |
| Uplisting | Built from docs | Built from docs |
| Smoobu | Built from docs | Built from docs |
| Hostfully | Built from docs | Built from docs |
| PriceLabs | Live-tested | Live-tested: date overrides and rule changes (write, verify, undo) |
| Beyond | Built from docs | Built from docs |
| RankBreeze | Live-tested | Never. Read-only |
| IntelliHost | Live-tested | Never. Read-only |


- **PriceLabs and Hospitable writes are live-tested** (2026-09-25: one night written, read back,
  undone and read back again; a PriceLabs day-of-week rule change the same way). Every other tool's change card says "first live write for <Name>:
  read the after-values carefully", and Claude says it out loud. Do that. We'll update this table
  as each tool gets its first real write.
- **Every change goes through one safe writer.** It checks nothing moved since the card,
  applies on your yes, reads it back, and can undo it in one step. It never pushes a change
  any other way. If it can't reach your tool, you get the exact change to make by hand.
- **The change goes where your prices live.** If PriceLabs or Beyond sets your prices, the
  change goes there. Your PMS only gets a price change when it sets the prices itself; the
  writer refuses otherwise, because your pricing tool would overwrite it on the next sync.
- **Gaps get named, not guessed.** Lodgify, Uplisting and Smoobu don't share reviews through
  their API. Lodgify and Smoobu don't share check-in or check-out day rules. No PMS shares
  its min price, so if your PMS sets your prices itself, Claude recommends a min and saves it
  before it will cut a price. Each gap is said at the top of the card, and it still prices.
- **Beyond** gets a real card too, with everything Beyond's API doesn't give (like market
  percentiles and the PriceLabs rule check) named up top.
- **RankBreeze, IntelliHost, AirROI, Turno, Breezeway:** optional and read-only. If you have
  them, they make the cards sharper. If not, it tells you what's missing and keeps going.

## What's in this folder

```
SETUP.md                  the setup Claude follows
install-tools.sh          checks Git, Node, uv and Python and installs what is missing
revenue-manager-plugin/   the Revenue Manager itself (skill, database tables, references)
mcp-servers/              connectors, if you ever run this without the connections kit
standalone/               the old all-in-one setup (legacy, not for the summit)
scripts/                  release checks (you won't need these)
```

## Your keys stay yours

This is a public folder and there are zero secrets in it. Your keys live in `.env` files
on your own computer, which are never committed, and they only ever go to the tool they
belong to. Never paste a key into the chat.

## Stuck?

Email Ryan: ryan.lefebvre@strsecrets.com

Built by Solnest AI.
