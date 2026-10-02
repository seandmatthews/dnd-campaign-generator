# 04 — Obsidian household onboarding

**Priority:** P2, small
**Area:** World Building tab (+ Review page-draft cards), `static/js/world.js`

## Problem

Lore is authored outside the app by design: entity pages are markdown under
`campaigns/<name>/lore/entities/`, and the studio reads disk. But a
first-time GM who wants to write their villain hits a wall — the app has no
"where do I type this?" answer. In the 2026-10-01 session the GM wrote the
Hollow Queen by opening the campaign folder directly, with no pointer from
the app.

## Ruling (do not revisit)

**No in-app lore editor. Not now, not later.** Obsidian is better software
than anything this repo will build, and the vault design (§1 of
`obsidian-integration.md`) already makes every page a first-class file.
This ticket is about making the *outside* editor discoverable, not
replacing it.

## Proposal

A small **"Authoring lore"** card (or dismissible tooltip, one per
campaign, remembered in localStorage, with a permanent 🔗 button in the
World Building header) that says, in plain words:

> This is now an Obsidian household. Every page you see here — entities,
> chronicles, chronicle links, the works — is a plain markdown file under
> `campaigns/<name>/lore/`. Write in Obsidian; the studio picks it up on
> reload.

The card carries exactly three affordances:

1. **The path** — the campaign's lore folder, with a copy button
   (`campaigns/<name>/lore/`), so the GM can point any editor at it.
2. **Open in Obsidian** — the fancy linky clicky thing: an
   `obsidian://open?path=<url-encoded absolute campaign lore folder>` link.
   With Obsidian installed this focuses Obsidian (first click on a new
   folder prompts Obsidian to open/add it as a vault — that one dialog is
   Obsidian's own and fine). Without Obsidian registered, the browser shows
   its standard "no handler" behavior — so the button is always paired
   with (3).
3. **Get Obsidian** — a link to <https://obsidian.md/download>.

Repeat the compact version on the Review tab's page-draft cards ("approve,
then open the folder in Obsidian to edit").

## Acceptance criteria

- [ ] World Building shows the authoring card (dismissible, remembered)
      with the exact lore path + copy button.
- [ ] "Open in Obsidian" issues the `obsidian://open?path=` URI for the
      campaign folder.
- [ ] "Get Obsidian" links to the official download page.
- [ ] Review page-draft cards carry the one-line pointer.
- [ ] No editing capability is added to the app (ruling above).
- [ ] A test asserts the URI is built from the *resolved* campaign path
      (spaces in `Documents\D&D` must survive URL encoding).
