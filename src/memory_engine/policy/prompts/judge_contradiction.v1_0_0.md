---
site: judge_contradiction
version: "1.0.0"
created_at: "2026-04-16"
parameters:
  neuron_a:
    type: string
    required: true
  neuron_b:
    type: string
    required: true
  entity_key:
    type: string
    required: true
---

Two claims about the same entity. Determine whether they contradict, complement, or refine each other.

Entity key: `{{ entity_key }}`

- **contradict** — the claims cannot both be true at the same point in time.
- **refine** — the newer claim adds precision or context to the older one; both remain true.
- **complement** — the claims are about different aspects of the same entity; both remain true independently.

If contradict, also identify which claim appears to be more recent based on wording (explicit dates, "now", "previously", "used to"). If no temporal signal, return `newer: null`.

Output JSON only:

```json
{
  "relation": "contradict|refine|complement",
  "reason": "<one sentence>",
  "newer": "a|b|null",
  "confidence": 0.0
}
```

--- CLAIM A ---
{{ neuron_a }}
--- CLAIM B ---
{{ neuron_b }}

Output the JSON object now, and nothing else.
