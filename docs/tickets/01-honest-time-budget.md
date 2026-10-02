# 01 — The time budget lies

**Priority:** trivial, high visibility
**Area:** TUTORIAL.md, README.md, in-app help text

## Problem

The tutorial promises "about 30 minutes" for a first campaign. Measured on
2026-10-01 (5-faction roster, real LLM provider):

- world generation: a few minutes
- one simulated week: ~7–10 minutes
- a 2-week forecast: ~10 minutes
- lore generation + approvals: a few minutes each, plus human reading time

A first campaign is an evening. The 30-minute claim makes a new GM feel like
they are doing something wrong when the first week takes longer than the
entire advertised tutorial.

## Proposal

Reframe the promise: **days or weeks of world history, compressed into a
couple of hours at the keyboard.** The machine works in minutes-long spans
while the GM reads what came back — say so, per step:

- `TUTORIAL.md` header: replace the **Time** line. Keep the per-step
  checkpoint style, and give every long step its expected duration
  ("this runs a headless map generator; a few minutes", "the week runs
  faction-by-faction; expect 10–20 minutes with a full roster").
- `README.md` ("in about 30 minutes"): same reframe.
- The in-app **?** help summary: same one-liner.
- Anywhere the app starts a long job, the button or panel should already
  say the expected duration (Maps: "a few minutes"; Sim: "10–20 minutes,
  roster-dependent") — the streaming log stays.

## Acceptance criteria

- [x] No document or help text claims a full campaign in 30 minutes.
- [x] Each long step states its expected duration before it starts.
- [x] The framing is "compressed hours," not "quick setup."
