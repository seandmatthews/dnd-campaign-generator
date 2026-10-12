# Running the table with Atlas VTT

A walkthrough from approved canon to a battle map on the screen at the
table — and the session's story back into the campaign afterward.

**Time:** about 20 minutes of setup the first time (Atlas install
included), then under a minute per session to get the map up. **Cost:**
everything here is free and offline except the last step — bringing notes
home runs the same metered pipeline as the tutorial's Step 6, and the app
asks before spending.

This page continues [`TUTORIAL.md`](TUTORIAL.md): that one ends with a
living campaign; this one is where you play it. The design background, if
you want it, is [`../design/atlas-seam.md`](../design/atlas-seam.md).

---

## What this is

[Atlas VTT](https://github.com/ByteMirror/atlas-vtt) is an Obsidian
community plugin that runs battle maps at the table: tokens, fog of war,
grids, dice, and a separate player view. It stores its whole world as
files inside your vault — and your campaign folder *is* a vault. So there
is no import step and no second copy of anything: the map files the
studio generated are the ones Atlas runs, and the notes you scribble at
the table are the ones the studio's review queue reads.

> The studio publishes *into* the campaign folder; Atlas watches the
> folder. The table writes *notes*; the studio's existing session
> pipeline reads them. Nothing syncs, nothing connects — files are the
> whole seam.

Every step below is the plain-file path you can always fall back on, not
a consolation for missing tooling. (The companion repo that automates
some of the copying now exists —
[dcg-avtt-integration](https://github.com/seandmatthews/dcg-avtt-integration):
its presets and scenes publishers are built — the scenes publisher hands
you the `.uvtt` to drag through its `_inbox/` whenever it can't confirm
the installed Atlas version against its pinned golden file. These manual
steps stay true either way — the publisher just does the dragging.)

## Prerequisites

| You need | For | Without it |
|---|---|---|
| [Obsidian](https://obsidian.md) desktop **1.8.7 or newer** | everything on this page | nothing here runs |
| The **Atlas VTT** community plugin, installed and enabled | the table itself | the campaign still works exactly as TUTORIAL.md describes |
| A campaign with at least one *battlemap scene* (TUTORIAL Step 3) | the map step | create one, or follow along with any `.uvtt` you have |
| A campaign with a *simulation* (TUTORIAL Step 5) | the last step only | notes can still be taken and imported; there is nothing for them to change yet |

## Step 1 — install Atlas

In Obsidian: **Settings → Community plugins** (turn off Restricted mode
if it asks) → **Browse** → search **Atlas VTT** → Install → **Enable**.

**You should see:** *Atlas VTT 0.6.0 or newer* in your installed
plugins, and an Atlas icon appear in the left ribbon. It is a desktop-only
plugin — it will not activate in Obsidian mobile.

*No Atlas in the community list?* Your Obsidian is older than 1.8.7 —
update Obsidian first (Settings → General → check for updates).

## Step 2 — open the campaign as the vault

Atlas reads your vault. The vault Atlas needs is the **campaign folder
itself**: in Obsidian, **Open another vault → Open folder as vault** →
pick `campaign-data/campaigns/<name>` (the folder holding `campaign.yaml`).

**You should see:** your campaign's files in the Obsidian sidebar —
`campaign.yaml`, `lore/`, `maps/`, `simulation/` — and the Atlas ribbon
icon active.

Two scope notes so nothing surprises you later:

- The World Building tab's *Authoring lore* card ("Register this folder
  as a vault") registers the campaign's `lore/` **subfolder** as its own
  small vault — that is for the lore deep-link only. For Atlas, open the
  campaign root by hand, as above.
- Your campaign folder is a git repo's working area (the studio commits
  it constantly), so a few dots-and-underscores folders are normal and
  can stay ignored.

## Step 3 — a system preset (ICRPG example)

Atlas has no rules engine — a *system preset* just tells it your table's
conventions: what a check rolls, what a heart is, which widgets the
toolbar carries. Presets are single JSON files that Atlas watches live in
`atlas-vtt/system-presets/` inside the vault.

Create the folder and drop this file in as
`atlas-vtt/system-presets/icrpg-house.json` (create any missing folders):

```json
{
  "format": 1,
  "id": "icrpg-house",
  "name": "ICRPG (house)",
  "rules": {
    "gridDefaults": { "unitType": "feet", "unitDistance": 5, "measurementMode": "metric", "diagonalRule": "equidistant", "abstractRangeBands": [] },
    "dice": { "defaultRoll": "1d20", "crit": "natural" },
    "initiative": { "mode": "sides", "roll": "1d20", "firstSide": "players" },
    "resources": [
      { "key": "hearts", "name": "Hearts", "field": "hp", "direction": "drains", "color": "#e11d48", "defeatedWhenSpent": true, "visibleToPlayers": true }
    ],
    "conditions": [
      { "id": "icrpg-blind", "name": "Blinded", "color": "#334155", "icon": "eye" },
      { "id": "icrpg-burned", "name": "Burning", "color": "#f97316", "icon": "flame" },
      { "id": "icrpg-dazed", "name": "Dazed", "color": "#7c3aed", "icon": "brain" },
      { "id": "icrpg-entangled", "name": "Entangled", "color": "#78716c", "icon": "guard" },
      { "id": "icrpg-feared", "name": "Feared", "color": "#991b1b", "icon": "terror" },
      { "id": "icrpg-prone", "name": "Prone", "color": "#0ea5e9", "icon": "drop" }
    ],
    "widgets": [
      { "id": "icrpg-alarm", "label": "Alarm", "icon": "stopwatch", "type": "timer", "value": 0, "duration": 60 },
      { "id": "icrpg-rounds", "label": "Round", "icon": "hourglass", "type": "counter", "value": 1, "min": 1 },
      { "id": "icrpg-progress", "label": "Progress", "icon": "clover", "type": "clock", "segments": 8, "value": 0, "showCount": true }
    ],
    "defaultWidgets": { "icrpg-alarm": true, "icrpg-rounds": true },
    "lightPresets": [
      { "id": "icrpg-torch", "name": "Torch", "unit": "feet", "bright": 40, "dim": 40, "color": "#ff9a3c", "animation": "torch", "kind": "torch", "sourceRadius": 2 },
      { "id": "icrpg-lantern", "name": "Lantern", "unit": "feet", "bright": 30, "dim": 30, "color": "#ffd28a", "animation": "none", "kind": "lantern", "sourceRadius": 1.5 }
    ]
  }
}
```

The values above are **one table's house rules, offered as a starting
point** — edit them to yours (the timer is ICRPG's alarm clock in
seconds; the condition list, colors, and lights are all yours to change).
Atlas ships built-in presets for several systems too: if your table runs
one of those, skip this file entirely and pick the built-in in the next
step. (The fields are Atlas's. The companion repo's presets publisher
installs and validates this exact file — `uv run publish.py <campaign>
presets` from a checkout of dcg-avtt-integration; it refuses unless your
campaign.yaml declares `system: icrpg`. Hand-editing stays optional
either way.)

**You should see:** in Atlas, open or create a collection
(**Atlas ribbon → New collection** — a collection is one table's world:
its maps, scenes, and tokens), open its **settings**, and find *ICRPG
(house)* in the **system preset** picker — no restart needed; the folder
is watched live. Select it: new tokens carry Hearts, and the Alarm and
Round widgets appear in the toolbar.

## Step 4 — the battlemap

Every battlemap scene the studio generates ships with a `.uvtt` file
beside its image — under
`campaign-data/campaigns/<name>/maps/sites/<slug>/` (scenes only; city
maps don't come in this flavor). Get it onto the table by hand:

1. Open Atlas's **Create maps** window (Asset Manager → Maps → the
   **+** → Add Map), then **drag the `<slug>.uvtt` in from your file
   manager**. Drag it — the *browse* button's picker only offers images,
   so the `.uvtt` is greyed out there (Atlas's filter, not a problem
   with the file). Everything travels inside that one file — picture
   included — so the `.png` beside it needs no separate import.
2. Walls, doors, and lights come with the file — that is what `.uvtt`
   carries. **You should see:** the scene on the grid at the right scale
   (5-foot squares per the preset), walls blocking movement and sight,
   doors clickable. Lights import *switched off* when the file says
   lighting is baked in — turn them on from the light menu, or flip the
   whole scene's lighting on in scene settings.

This is the always-true path: it works on a fresh install with nothing
but the files the studio already made. (It is also exactly what the
companion repo's *scenes publisher* automates — landing the map, scene,
and lighting into a collection for you. While its golden-fixture capture
is pending it hands you the `.uvtt` through its `_inbox/` and you do
this drag. The drag is the feature either way.)

> **If Atlas says the walls are too crowded.** When you drag in a
> `.uvtt`, Atlas checks the map's walls before importing. The notice
> "Could not import `<file>`. The walls in the file end too close
> together in too many places for Atlas to join them." means the walls
> are too finely chopped — not that the file is corrupt. To stop light
> leaking through hairline gaps, Atlas welds together wall ends that sit
> within about 13 pixels of each other (a bit more on very large maps).
> A map whose walls are drawn as thousands of tiny pieces — typical of
> auto-generated maps that put a wall stub on every tile edge — packs so
> many near-touching ends together that this welding becomes a job Atlas
> refuses to take on. Nothing was imported and the file is untouched, so
> retrying is safe. Fix it in the map's source: redraw the walls as a
> few long lines per room instead of one segment per tile edge, delete
> leftover duplicate segments, export a fresh `.uvtt`, and drag it in
> again. (A related refusal, "The file has more than 20,000 wall
> segments.", means the same fix.) The repo's `publish` command never
> hits this — it writes the scene file directly, past the importer.

## Step 5 — two screens

Atlas splits the table: **GM view** (everything — fog revealed, all
tokens) and **player view** (what the party may see). Put the player
view on the second screen and keep the GM view to yourself; players
interact with their view only.

**You should see:** the player view showing fog where the party hasn't
been, and your GM view showing the whole scene. What you reveal in one
appears in the other, live, with no network — same machine, same files.

Everything you tune at the table — moved doors, light levels, revealed
fog — lives in the scene files under the campaign's `atlas-vtt/` folder,
and the campaign's git history snapshots those files like everything else:
your tuning is an artifact, diffable and recoverable, never silently
regenerated away. (When the planned scenes publisher arrives, it will
refuse to overwrite a tuned scene rather than clobber it — until then,
the hand drag of Step 4 is the path, and what you tune by hand stays
yours.)

## Step 6 — play: pin the session note

Keep a running note during the session and pin it to the scene (open any
note in the vault, then use the **pin** control so it floats over the
map). Rough, past-tense, exactly as scribbled — the same raw material as
the tutorial's Step 6:

    The party burned the Mint's warehouse at the east quay…

Keep table notes in their own folder — `table-notes/` in the campaign
root works well (the `sessions/` folder belongs to the studio; don't
hand-place files there). The note is ordinary markdown: edit it in
Obsidian, in Atlas's floating panel, or not at all until after play.

**You should see:** your note beside the map, scrolling as you type,
while the fight runs on the screen behind it.

## Step 7 — bring the story home

After the session, hand the note to the pipeline that already exists:

    python studio/session_notes.py import <name> \
        --title "The east quay fire" \
        --notes-file "campaign-data/campaigns/<name>/table-notes/2026-10-10.md"

(Prefer the app? The **Sessions** tab does the same thing — paste or
edit the note there; it is the same store and the same review flow.)

Then the metered part, each step the app asks before spending:

1. `python studio/session_notes.py extract <name> <session-id>
   --confirm-spend` — one LLM call proposes changes from the note.
2. Review the cards (Sessions tab or `prepare --select`) — each shows the
   quotation it stands on; tick what you accept.
3. `python studio/session_notes.py prepare <name> <session-id> --all`
   stages ONE proposal; **accept it** there or with
   `python studio/session_notes.py accept <name> <proposal-id>`.

**You should see:** the session in the Sessions list with its draft, and
after acceptance the decision record citing your note's revision — the
next simulated week inherits the consequences, and nothing entered canon
that you didn't approve. (Full detail on the review cards:
[TUTORIAL.md Step 6](TUTORIAL.md#step-6--after-play-session-notes-to-campaign-changes).)

## Troubleshooting

| Symptom | What it means | Fix |
|---|---|---|
| Atlas won't enable / doesn't appear | Obsidian older than 1.8.7, or Obsidian mobile | Update Obsidian desktop; the plugin is desktop-only |
| The preset doesn't show in the picker | File not under the vault's `atlas-vtt/system-presets/`, wrong extension, or JSON typo | The folder is watched live — fix the path and it appears without a restart; a file with no usable `id` is left out silently |
| Imported map has no walls/doors/lights | The `.uvtt` didn't come along (only the PNG did), or the import ran without it | Drag the `.uvtt` into the Create-maps window — every generated *site* has one beside its PNG, and the picture is embedded in it |
| Can't pick the `.uvtt` in the browse dialog | The browse picker filters to images | Drag the `.uvtt` in from your file manager instead — the drop accepts `.dd2vtt`, `.uvtt`, `.df2vtt` |
| Map is there but everything is dark | The `.uvtt` says lighting is baked, so imported lights start off | Turn lights on in the light menu, or enable scene lighting in scene settings |
| Tokens have no Hearts | The collection's system preset isn't set (or you edited the preset's `id`) | Pick the preset in collection settings; changing `id` in the file makes Atlas treat it as a different preset |
| `import` says nothing / wrong file | The path given to `--notes-file` isn't the note you pinned | Give the full path to the pinned note; import copies it into the studio's session store as revision 1 |
| `extract` refuses: no LLM API key | The metered step needs the key | Set it in `llm.env` (see TUTORIAL Prerequisites); you can add suggestions by hand instead |
| `prepare` refuses: another proposal pending | A proposal is already waiting in Review | Resolve that one first; the session keeps its draft |

## Where this is going

The seam is deliberately files-only, so every step above survives whatever
tooling comes and goes. The companion repo —
[dcg-avtt-integration](https://github.com/seandmatthews/dcg-avtt-integration)
— is real: the presets publisher (Step 3) and the scenes publisher ship
today (the scenes publisher refuses to overwrite a map you've hand-tuned,
and falls back to this page's drag when it can't pin the installed Atlas
version). Still planned: ICRPG fence templates for the statblock
convention the studio already carries. This page updates as those land —
the manual paths above stay true either way.
