# Week 2 — The week of paper and blood

The council granted the Mint its petition on Tuesday. By Wednesday, a clerk
of House Ferrand sat at the customs house with the estate's seal on his desk,
and every crate that came off a ship was logged twice — once for the harbor,
once for the debt. The Doge is dead; his revenue now works for his creditors.

The Reliquary gave the city the funeral it was promised. The procession went
through the Docks at noon, the empty casket bright under cloth of gold, and
the alms boxes came back heavy. The church called it a triumph of the
Interred's mercy. The city, having paid, now expects the soul-debt to be
settled publicly — and soon. What the congregation did not see was anything
to be buried.

The Bastard chose the same week to go looking for her mother's letters in the
sealed manor. The Mint's watchers took her at the garden stair — the same
watchers the Mint now posts everywhere it holds paper. She was released by
evening with no charges and no letters, and the whole Quarter knows she went
looking for something. She came out knowing one thing worth having: the
watchers' rotation.

The harbor refused. The longshoremen would not touch the estate's cargo until
back wages were posted — and the Mint, holding the customs house by council
order, simply hired from the Tanneries. By Friday the estate's ships were
loading again. Men who came to stand together watched half their number get
replaced in an afternoon; the guild's word in the Docks is suddenly worth
less, and everyone knows whose signature made it so.

And in the crowd at the funeral, two Marsh Gang pickpockets went for a
money-belt and found instead the wrist of a city watchman. Two of the thirty
now sit in the Doge's cells awaiting the assizes. The Gang does not blame
itself. The Gang blames the church, whose procession, in its telling, was a
trap baited with gold.

---

## Behind the screen

Proposals (each faction acting on its fog-of-war view only), resolved by
`src/resolve.py` — d20 + committed-resource bonus vs risk DC. Outcome rule:
margin ≥ +5 full gain; 0 to +4 gain with a complication; −1 to −5 wasted
week; ≤ −5 loss or exposure.

| faction | action | roll+bonus vs DC | margin | outcome |
|---|---|---|---|---|
| mint | petition council to attach customs revenue | 15+3 vs 13 | +5 | claim of record; legitimacy → high |
| reliquary | grand funeral procession, alms toward arrears | 13+3 vs 13 | +3 | procession succeeded; complication: public expectation of soul-debt resolution |
| bastard | search sealed manor for mother's letters | 1+2 vs 16 | −13 | caught by Mint watchers; gained knowledge of their rotation |
| longshoremen | refuse estate cargo until wages posted | 2+3 vs 13 | −8 | strikebroken from the Tanneries; muscle → medium |
| marsh_gang | rob the procession | 4+1 vs 16 | −11 | two taken by the watch; disposition reliquary → hostile |

Process notes for the M1 wiring:

- Proposals were written from each faction's view alone; no secret crossed
  between them. The dramatic collisions (gang vs procession, watchers vs
  Bastard) were emergent, not scripted.
- The resolver rolls each action *independently*. The Gang targeted the
  procession, but interference between actions is currently narrated by hand.
  An opposed/interference rule is the next mechanical need — note for M2.
  *(Added same day, after this chronicle was written: contested actions now
  resolve in code — `resolve_tick` in `src/resolve.py`; if an action targets
  a faction that acted the same week, higher effective total wins, ties to
  the defender. Tests: `tests/test_resolve.py`. Replayed with this rule, the
  procession holds, 21 to 16.)*
- The Mint's success (+5) would have bound the customs revenue even if the
  longshoremen had succeeded; the strike failed against a creditor who now
  holds the thing being struck. Consequences compose; that is the point.
- The Mint's backdated-instrument secret is still unknown to every faction.
  It is now attached to a claim of record, which raises the price of the
  clerk's silence.
