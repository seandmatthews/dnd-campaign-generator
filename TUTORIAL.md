# Your first campaign

A walkthrough from zero to a living campaign: a generated world, city and
battle maps, lore you approved, and a faction simulation that writes a
chronicle week by week.

**Time:** about 30 minutes the first time (including installs); roughly 15
once your machine is set up. **Cost:** everything except two features is
free and offline. Story generation and week simulation call an LLM and
spend API credits — the app asks before every one of those.

---

## What this is

Campaign Studio is a **workbench for running a D&D campaign's world**. You
give it a seed, and it grows a world: maps, geography, lore. You populate
it with factions that want things from each other, and every week it
simulates what they do about it — proposals, dice, rumors, consequences —
and writes the results as a chronicle you can read at the table.

You are the DM. The machine *proposes*; **nothing enters the campaign
until you approve it** in the Review tab. A campaign is one folder of
readable YAML and Markdown under `campaigns/<name>/` — no database, no
cloud, portable by zip. Every change is auto-committed to git, so history
is your undo.

It runs entirely on your machine (`http://127.0.0.1:8765`, localhost only).

---

## Prerequisites

| You need | For | Without it |
|---|---|---|
| Python 3.10+ | everything | nothing runs |
| `pip install -r studio/requirements.txt` — or, if you use [uv](https://docs.astral.sh/uv), nothing: `uv run` sets the packages up by itself | everything | the app won't start |
| Node 18+ (npm comes with it) | *world, city and battlemap generation* | you can still create a campaign, write lore by hand, and run the simulation |
| An **LLM API key** — any OpenAI-compatible provider works (Z.ai, OpenAI, OpenRouter, or a free local [Ollama](https://ollama.com)) | *backstory generation and live weeks* | everything else still works — browse, edit, approve |

**The key** lives in `llm.env` at the repo root: copy `llm.env.example` to
`llm.env`, uncomment one provider block, and fill in the key. The file is
git-ignored — the key never leaves your machine except to your chosen
endpoint. Environment variables (`LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL`) override the file. With no llm.env at all, the studio falls
back to its built-in Z.ai defaults: a **coding-plan** key from
`ZAI_API_KEY` or `~/.zcode/v2/provider_config.json` (the pay-as-you-go
endpoint fails with error `1113` even with balance available).

Not sure what's missing on your machine? Run the doctor — it checks every
prerequisite and tells you the exact fix:

    python studio/doctor.py

---

## Step 0 — install and boot

From the repo root (the folder containing this file):

    pip install -r studio/requirements.txt
    python studio/run.py

If you use [uv](https://docs.astral.sh/uv), the two lines collapse to one
(`uv run` creates the environment and installs everything from `uv.lock`
by itself):

    uv run python studio/run.py

You should see:

```
Campaign Studio serving at http://127.0.0.1:8765
```

and your browser opens the app by itself (if it doesn't, open
<http://127.0.0.1:8765> manually — the console window must stay open).
On Windows you can instead double-click `studio\start.bat`, which finds
Python and offers the pip line for you. `Ctrl+C` stops the server.

If anything fails to start, run `python studio/doctor.py` and do what the
FAIL lines say.

**You should now see:** a page titled *Campaign Studio* with four tabs —
Maps, World Building, Agent Simulation, Review. If a colored strip appears
under the header listing setup notes (missing Node, missing API key), that
is the app telling you what will be limited — nothing is broken. New to
the page? The **?** button in the header has a two-minute summary.

## Step 1 — create the campaign

Click **+ New** in the header. Name it a lowercase slug (letters, digits,
hyphens — e.g. `ember-coast`); the title is optional.

**You should see:** a toast "Campaign created…" and the picker jumps to
your new, empty campaign.

## Step 2 — generate a world

On the **Maps** tab, find *Generate a world*. Type any seed (e.g. `24601`)
and click **Generate world**. This runs a headless map generator; it takes
a few minutes and streams its log into the page.

**You should see:** a toast "World generated: N nations, M settlements",
a world map at the top of the tab, and the *Map a burg* form now filled
with towns. ("Burg" just means a town on the world map.)

*No Node installed?* This step needs it — see the table above and the
detailed tool recipe in [`dnd-mapping/docs/map-pipeline.md`](dnd-mapping/docs/map-pipeline.md).
Skip to Step 4 (lore by hand) or Step 5; you can come back later.

## Step 3 — maps: a city and a battlemap

Still on Maps:

- **Map a burg** — pick a town, click **Map it**. A street-level city map
  appears in the *Cities* grid. The **attach to place** field (it follows
  the burg unless you edit it) links the map to that place's entity page —
  "everything about this place" now includes the map.
- **Battlemap scenes** — pick a kind (dungeon / cave / camp), give it a
  name (e.g. "The Drowned Crypt"), optionally attach it to a place, click
  **Generate scene**. You get a scene image plus a `.uvtt` file under
  `campaigns/<name>/maps/sites/…` — drop that into Foundry VTT and walls,
  doors and lights come with it. Each map also gets a small companion note
  (`<slug>.md`) embedding the image and linking its place; open the
  campaign folder in Obsidian and the links are live.

**You should see:** thumbnails in both grids; the files on disk under
`campaigns/<name>/maps/`.

## Step 4 — lore: backstory, names, and the Review queue

Now the LLM features (API key needed). On **World Building**:

- **Generate backstory** — optionally give it a focus ("the salt tax
  riots of the Docks"), confirm the credits prompt. After a minute it
  stages 3–6 proposed timeline entries.
- **Name helper** — type a culture name and ask for suggestions. The
  helper refuses to invent names from nothing: the first attempt invites
  you to *stage a seed list* (3+ names, one per line) that it will then
  draw from in-voice.

Open the **Review** tab. Everything staged is waiting there, with
verification findings attached as advice. **Approve** writes an entry into
the campaign's timeline; **Delete** discards it. Nothing has touched your
campaign until you decide. Entities are markdown pages now
(`lore/entities/One Page Per Entity.md`): click an entity in World
Building to read it with its backlinks ("mentioned in"), and hand-edit the
pages — or open the campaign folder in Obsidian and use wiki-links there.
Dangling links are not errors: they appear in Review as findings, and the
**Write the missing pages** card on World Building turns each one into a
draft page grounded in the pages that mention it (you approve that too).
When you approve generated lore, names it coined get small stub pages so
the graph keeps up with the story — and every new week's chronicle is
linkified, so an entity's backlinks show where it surfaced in the story.

**You should see:** your approved entries in the Timeline panel of World
Building, tagged with their source.

## Step 5 — the simulation: factions and the first week

On **Agent Simulation**, a fresh campaign shows *Create a simulation*:

1. Write a **premise** — the tension, one line. E.g. "The toll bridge
   burned the night the wardens raised the toll."
2. Add **2–5 factions** — a name and, most importantly, a **goal**: what
   that faction wants. Give them coin / muscle / legitimacy levels
   (none / low / medium / high). Asymmetry is interesting: a high-status
   faction with no muscle vs. a scrappy one makes a story.
3. Click **Create simulation**. Faction cards appear. This step is free —
   it's your setup, no LLM involved.

Then click **Propose week 1** (under *Generate week 1*). After a credits
confirmation, the week runs (a few minutes): factions act on what they can
*see* (their fog of war, inspectable in the Views panel), dice decide
contested moves, and rumors spread. What comes back is a **proposal** —
the proposed week with its chronicle and a diff, shown right on the tab.
Nothing is canon yet: read the chronicle, check the diff, then click
**Accept** to publish it (or **Discard** to throw the whole week away and
propose a fresh one).

The scheduler treats a sandbox's first week as founding week: every
faction acts, hungry or cautious. Later, a faction the world has stopped
touching will sit a week out (holds are announced in a banner, and the
chronicle narrates quiet weeks just as faithfully). To keep a rivalry
hot, mark it in `simulation/state.yaml`:
`disposition: {other-faction-id: hostile}` under a faction.

**You should see:** after accepting, the week clock advance, the
chronicle in the Chronicle reader, and the events in the proposal's diff
now part of canon. Curious why a faction did something? The **Why
inspector** traces its causal chain. New: on the **Knowledge** tab you can
record facts with explicit visibility (DM-only, public, faction-only),
read the deterministic DM briefing, and reveal public facts to the party
handout.

Run more weeks whenever you like — the world accumulates history. Your
players never see the machinery; you harvest maps, lore and rumors from it.

## Step 6 — where everything lives, and your next campaign

    campaigns/<name>/
      campaign.yaml          # name, title, created
      world/                 # geography.yaml, the map generator's dump, map.svg
      lore/                  # timeline.yaml, entities/ (one markdown page per
                             # entity, wiki-linked), style/voice.md
      simulation/            # state.yaml (the sandbox), chronicle/*.md
      maps/                  # cities/<slug>/, sites/<slug>/ (incl. Foundry
                             # .uvtt and each map's companion note <slug>.md)

Every generation, approval and week auto-commits just that campaign folder
to the campaigns workspace's own git repo — `git -C campaigns log -- <name>`
is the audit trail, and a bad outcome is one checkout away from gone.

Starting another campaign is Step 1 again. A fresh clone starts with an
empty `campaigns/` folder — campaigns you create live there, and nothing
in them is precious: every campaign folder is its own git history, so you
can always check out yesterday's world.

---

## Troubleshooting

| Symptom | What it means | Fix |
|---|---|---|
| Browser shows nothing / can't connect | The server window closed, or a different port | Restart `python studio/run.py`; the console prints the URL (`STUDIO_PORT` env overrides the port) |
| "not importable: fastapi …" at boot | Python packages missing | `pip install -r studio/requirements.txt` (or `python studio/doctor.py --fix`); with uv: `uv sync` |
| World generation fails; log mentions node / vitest / FMG | Node missing, or the map tool tree isn't set up | Install Node (npm comes with it); see `dnd-mapping/docs/map-pipeline.md` and `python studio/doctor.py --fix`. Everything else works meanwhile |
| "no LLM API key found" | The key isn't visible to the app | Copy `llm.env.example` to `llm.env` and set `LLM_API_KEY` (any OpenAI-compatible provider, or a local Ollama); the legacy `ZAI_API_KEY` / `~/.zcode/v2/provider_config.json` also work |
| Z.ai error `1113` "insufficient balance" | The key is not a **coding-plan** key | Use a coding-plan key (requests must hit the coding endpoint) — or switch providers in `llm.env` |
| A 422 saying "fed or refused" | Generation needs ground data that doesn't exist yet | Do the named prerequisite first (usually: generate a world) — the app never generates from nothing |
| "another job is already modifying this campaign" | A previous job is still running | Wait for it (its log streams in the panel); one mutation at a time per campaign |
| A job failed and the log is raw engine output | The engine hit an error; the tail is shown on purpose | Read the last lines; `python run-tests.py` checks the wiring; engine docs are in each package's README |
| You ran a week and "nothing happened" | Factions without a visible API key just *hold* | Check the key (Step 4); the week log shows each faction's proposal or hold |

Stuck? `python studio/doctor.py` diagnoses the machine-level stuff; the
studio's own [`studio/README.md`](studio/README.md) covers architecture
and the API contract.

## Glossary

- **burg** — a town on the world map (map-generator terminology).
- **canon** — what's actually true in your campaign: the approved files.
  Every canon change is git-committed.
- **chronicle** — the Markdown story of one simulated week, with its roll
  table.
- **fed-or-refused** — the guardrail that generation only runs when it has
  ground data (a world, a seed list) to draw from. It never invents from
  nothing.
- **fog of war** — a faction only acts on what it can see; the Views
  inspector shows exactly that.
- **pending** — staged material awaiting your decision in Review. Nothing
  lands in the timeline unapproved.
- **spine** — the campaign's canonical files on disk (`world/`, `lore/`,
  `simulation/`, `maps/`).
- **week** — one tick of the simulation: factions propose, dice resolve,
  the chronicle is written.
