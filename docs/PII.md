# PII Handling — design evolution

[← back to README](../README.md) · [Architecture overview](../ARCHITECTURE.md)

Conversation messages contain user PII (emails, phone numbers, cards). How they are stored evolved
through three deliberate versions, and the journey is the point.

- **V1 — raw text.** `messages.content` stored exactly what the user typed. Simple, but a privacy
  gap: a leaked DB backup or a support engineer with read access sees raw PII.
- **V2 — irreversible redaction (rejected).** Run the regex redactor over the message before
  storage, keep only `<EMAIL>` / `<PHONE_IN>`. Closes the privacy gap — but **breaks the product**.
  On resume, the conversation history fed back to the LLM is now redacted, so the model loses the
  context it had on the first turn ("ship it to the address I gave you" → the address is gone). The
  fix cannot destroy information the conversation depends on.
- **V3 — reversible tokenization (implemented).** Replace each PII span with a stable token
  `[PII:TYPE:N]` and store the original values in a nullable `pii_map` JSONB column on the same row.
  The stored content is de-identified, *and* it is losslessly reversible.

```
  user types:  "email me at a@b.com or call 9876543210"
                         │ tokenize()
                         ▼
  stored content:  "email me at [PII:EMAIL:1] or call [PII:PHONE_IN:1]"
  stored pii_map:  { "EMAIL:1": "a@b.com", "PHONE_IN:1": "9876543210" }

  LLM context  ◄── tokenized content (PII never reconstructed for the model)
  user display ◄── detokenize(content, pii_map)  →  original text
```

> **Why square brackets `[PII:TYPE:N]` and not `<pii:TYPE:N>`?** Angle-bracket tokens read as XML
> tags to LLMs, which made models echo them back literally in replies. Square brackets are inert to
> the model. The detokenizer regex matches only the `[PII:...]` form; any legacy `<pii:...>` rows are
> left untouched (detokenize never raises) and would be fixed with a one-off backfill in production.

**Why a JSONB `pii_map` on the message row** (rather than a side table):

- *Schemaless* — every message has a different shape of PII; JSONB needs no migration per type.
- *Atomic* — the map lives with the message it belongs to; one read, no join, no orphan risk.
- *Nullable* — a clean message stores `NULL` and costs nothing (the GIN index is partial,
  `WHERE pii_map IS NOT NULL`).
- *Queryable* — the GIN index supports "which messages contain a redacted card" style audits.

**The detokenization boundary is exactly one place.** The LLM *always* sees tokens — the chat
service builds context straight from the tokenized stored content and never reverses it. The user
*always* sees originals — detokenization happens only in the `GET /conversations/{id}/messages`
display route. There is no path where raw PII is reconstructed for the model.

**Scope.** Only **user** messages are tokenized — and the conversation title, which is derived from
the tokenized user text so PII never lands in a title either. Assistant messages are model output,
not user PII, and are stored as-is (an assistant echoing PII back is a separate output-scanning
concern). Observability previews (`input_preview` / `output_preview`) keep using the irreversible
regex redactor — they are telemetry, not conversation context, so they never need to be reversed.

**A note on reversibility.** The `pii_map` is plaintext in Postgres today, so the privacy win is
**path isolation**, not encryption: raw PII never enters `inference_logs`, the ingestion service, the
dashboard, or any provider payload — it lives in exactly one column behind one access boundary.
Production would wrap that column in pgcrypto/KMS and restrict grants, and the same structure enables
right-to-erasure (drop the map → tokens dangle harmlessly). Reversible tokenization was chosen over
destructive redaction precisely because the chatbot must still be able to answer about the user's own
data.

Sources: Microsoft Presidio's reversible tokenization (anonymize → deanonymize with an entity
mapping); the general tokenization-vs-masking distinction (masking is one-way, tokenization is
reversible via a secured map).
