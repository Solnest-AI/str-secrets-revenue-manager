# Revenue Manager for STR Secrets Summit 2.0

Your AI revenue manager, running inside Claude Code. It reads your real PMS calendar and
your pricing tool, checks the whole booking picture first, and tells you exactly what to
change. It tells you what your min price should be too. You don't have to guess it.

And nothing changes on its own. It shows you every change on a card and asks. You say yes,
it applies it, then it reads it back to prove it took. You say no, nothing happens.

## Before you start

Do the **STR Secrets connections kit** first. That's the setup that connects your PMS,
your pricing tool and your database, and puts your keys in the right place. If you already
said "Set up my connections" and got a green board, you're good.

## Setup

Open Claude Code in this folder and say:

> **Set up the revenue manager.**

Claude follows `SETUP.md`, in this order:

1. Checks your connections.
2. Installs the plugin.
3. You fully quit and reopen Claude Code (Cmd+Q on Mac, not just closing the window). The
   skill only loads on a fresh start.
4. You say "finish the revenue manager setup". Claude asks you one question (what markup you
   add per channel) and runs your first check.

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

- **Hospitable, Guesty or OwnerRez, plus PriceLabs:** the full tested runner. Setup maps
  every property for you and tells you about any it can't map.
- **Hostaway, Lodgify, Uplisting, Smoobu, Hostfully:** no tested runner yet. Claude pulls
  the data through the tools the connections kit connected and applies the same safety
  layer and the same card layout itself. It works, but it leans on Claude doing more by
  hand, so check the numbers on your first run.
- **Price changes:** every PMS and both pricing tools (PriceLabs, Beyond) can take changes.
  Every change goes through one safe writer: it checks nothing moved since the card, applies
  on your yes, reads it back, and can undo it. If the writer can't reach your tool yet, you
  get the exact change to make by hand instead. It never pushes a change any other way.
- **Beyond:** reads work. Writes go through the same safe writer. Reading each Beyond change
  back is coming with the writer's Beyond support; until then you get the exact change to
  make in Beyond yourself.
- **RankBreeze, IntelliHost, AirROI, Turno, Breezeway:** optional and read-only. If you have
  them, they make the cards sharper. If not, it tells you what's missing and keeps going.

## What's in this folder

```
SETUP.md                  the setup Claude follows
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
