# Placeholder Pollution — Fixes Needed in poster2json

These placeholder values are injected by `SchemaConverter._ensure_required_fields()` when repository metadata is missing a field. They pollute the corpus with fake data that looks real but is meaningless. All should be removed upstream so missing data stays missing.

## Placeholder values to strip

| Field | Placeholder value | What to do instead |
|-------|-------------------|-------------------|
| `identifiers` | `[{"identifier": "unknown", "identifierType": "Other"}]` | Omit field entirely or `[]` |
| `creators` | `[{"name": "Unknown"}]` | Omit field — `creators` is required by schema but "Unknown" is worse than empty |
| `titles` | `[{"title": "Untitled Poster"}]` | Omit or use `null` title |
| `publisher` | `{"name": "Unknown"}` | Omit field |
| `subjects` | `[{"subject": "Scientific Poster"}]` | Omit — not a real subject keyword |
| `dates` | `[{"date": "<current_year>", "dateType": "Issued"}]` | Omit — fabricated date is worse than missing |
| `language` | `"en"` (hardcoded default when missing) | Omit or `null` — assuming English is wrong for a multilingual corpus |
| `types` | `{"resourceType": "Scientific Poster", "resourceTypeGeneral": "Image"}` | Keep as default — this one is actually correct for posters |
| `formats` | `["PDF"]` | Keep as default — most posters are PDFs |
| `rightsList` | `[{"rights": "All rights reserved"}]` | Omit — assuming all-rights-reserved is legally incorrect if actual license exists |
| `descriptions` | `[{"description": "Scientific poster", "descriptionType": "Abstract"}]` | Omit — fake abstract pollutes description field |
| `fundingReferences` | `[{"funderName": "Not specified"}]` | Omit entirely — "Not specified" funder is garbage data |
| `conference` | `{"conferenceName": "Not specified", "conferenceYear": <pub_year>}` | Omit — "Not specified" conference name is garbage |
| `conference.conferenceYear` | `<publicationYear>` fallback | Don't backfill — publication year != conference year |

## Additional issues in `_normalize_language()`

- Falls back to `"en"` for unrecognized inputs (line 51) — should return `null`
- Falls back to `"en"` for empty string (line 43) — should return `null`

## Recommendation

Replace `_ensure_required_fields()` with a validation-only function that:
1. Logs warnings for missing required fields (don't fabricate data)
2. Only sets truly universal defaults (`types`, `formats`)
3. Leaves everything else as `null` / omitted when data isn't available

The merger and downstream consumers should handle missing fields gracefully rather than relying on fake placeholders.
