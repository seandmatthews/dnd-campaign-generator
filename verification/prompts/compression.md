# Compression auditor

Charge: find prose that compresses to nothing. For each paragraph of the
TEXT, ask: "what changes at the table if this paragraph is deleted?" A
paragraph earns its place if its deletion loses a state reference, a
callback, a consequence, or a concrete sensory fact a DM could use. If
deleting it loses nothing — no fact, no hook, no callback — it is filler.

TEXT:
{{TEXT}}

Output: a JSON array, nothing else. One object per filler paragraph:
[{"rule": "compression", "quote": "<first six words of the paragraph>", "note": "<what nothing it does>", "severity": "warning"}]
Empty array if every paragraph earns its place. Quote exactly.
