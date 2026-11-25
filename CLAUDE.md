# Revenue Manager (STR Secrets Summit 2.0)

- If asked to set up, follow `SETUP.md`. If asked to finish the setup, go to step 3 of
  `SETUP.md`. No restart is needed between installing the plugin and the first run.
- If asked about pricing, revenue, occupancy, rates or rankings, use the `revenue-manager`
  skill.
- Never ask for, print or paste a key in the chat. Keys live in `.env` files on this
  computer only.
- Never change a price without showing the change first and getting a plain yes.
- **Never commit, push, fork, open a pull request or create a GitHub repo for this folder.** It is
  the user's own copy of a tool, not a project. When they say "save", everything is already saved
  on disk: say so. (The developer note below is the only exception.)
- If asked to change how the revenue manager works for them (wording, branding, what a card
  includes), follow "Your own changes" at the top of the skill's `SKILL.md`. Never edit this
  folder's files for that: updates replace them.
- If asked to update the revenue manager, follow the next section.

## When the user says "update the revenue manager"

Updates normally install themselves (step 2 of `SETUP.md` turns that on), so this is only
needed when the user asks. Their copy is a plain folder, not a git clone (step "First" of
`SETUP.md` unlinks it), so it updates by downloading the latest published release. Their `.env`
files, connector logins, installed tools and caches are never touched. Run each command from
this folder (the one this file is in).

1. **A folder that still has a `.git` folder inside** was cloned before 2026-10-10. Bring it up to
   date the old way one last time, then unlink it. A download over a clone looks like local
   edits, and the script would then keep it linked:
   `git pull --ff-only && bash scripts/detach_from_github.sh`
   If the pull refuses, the copy has its own edits: say so and stop.
2. **Any other folder:** get the latest release:

   ```bash
   uv run --no-project --python 3.13 python scripts/update.py
   ```

   It keeps every file it replaces in `.update-backup/`, and
   `uv run --no-project --python 3.13 python scripts/update.py --undo` goes back one step.
3. Run step 2 of `SETUP.md`. It reinstalls the plugin from this folder (its line shows the new
   version) and turns automatic updates on again, which picks up a newer auto-updater if the
   release has one.
4. Tell them the version they are on now, and to quit and reopen Claude Code so it loads (the
   restart words in step 0 of `SETUP.md`).

"Is it updating itself?":
`uv run --no-project --python 3.13 python ~/.solnest/autoupdate.py status`. Stop all automatic
updates: `... autoupdate.py pause` (`resume` turns them back on).

When a pricing card ends with `UPDATE AVAILABLE`, automatic updates are off or failing on this
computer. Run `autoupdate.py status`, tell the user what it says, and offer to update now.

## Developer note

Working on this repo's code (not running it)? The GitNexus instructions are in
`docs/DEV.md`.
