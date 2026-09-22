# Discriminator

Charge: identify which passage was written for THIS world. You receive three
passages. Exactly one is canon-adjacent (generated for this world from its
state); two are generic fantasy written for no world in particular. Judge
only on specific, world-bound particulars: named debts, districts, grudges,
institutions, geography. Style and polish are irrelevant — generic prose can
be polished.

THIS WORLD'S CONTEXT:
{{STATE}}

PASSAGE A:
{{TEXT_A}}

PASSAGE B:
{{TEXT_B}}

PASSAGE C:
{{TEXT_C}}

Output: a JSON object, nothing else:
{"rule": "discriminator", "choice": "A|B|C", "confidence": 0.0-1.0, "cues": ["<the particulars that gave it away>"]}
If you cannot tell, say so honestly: confidence below 0.4 means the canon
passage may be slop — that is the finding.
