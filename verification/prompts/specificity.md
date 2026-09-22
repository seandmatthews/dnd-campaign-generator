# Specificity rewriter

Charge: find sentences that could appear in any D&D campaign and rewrite
them to belong to THIS one. A sentence is generic if no particular of this
world anchors it — no named institution, debt, district, grudge, season, or
consequence. Rewrite each generic sentence with at least one true particular
from the CONTEXT, or mark it "justified" if genericity is correct there
(transition lines, table mechanics, voice asides).

CONTEXT:
{{STATE}}

TEXT:
{{TEXT}}

Output: a JSON array, nothing else:
[{"rule": "specificity", "quote": "<exact generic sentence>", "rewrite": "<anchored version>", "note": "<justified | what the rewrite anchors>", "severity": "warning"}]
Empty array if nothing is generic. Never invent particulars the CONTEXT
does not back — anchor with what exists or leave the sentence alone.
