---
site: summarize_episode
version: "1.0.0"
created_at: "2026-04-16"
parameters:
  events_text:
    type: string
    required: true
  summary_max_words:
    type: integer
    required: false
---

Summarize the following conversation excerpt into a single paragraph. The summary will be stored as an episodic memory; it must be faithful to the events, not an interpretation.

Rules:
1. Third-person, past tense.
2. Name participants by their role: "the persona", "the counterparty". Do NOT use first names unless they appear verbatim in the events.
3. Do NOT infer motivations or emotions beyond what is explicitly stated.
4. Do NOT add information not in the events.
5. Maximum {{ summary_max_words | default(60) }} words.
6. If the events contain sensitive information (passwords, credentials, secrets), summarize their existence without quoting them.

Output the summary as plain text, a single paragraph. No JSON, no lists, no markdown.

--- BEGIN EVENTS ---
{{ events_text }}
--- END EVENTS ---

Write the summary now:
