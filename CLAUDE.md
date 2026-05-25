# Zotero Classification Pipeline

Automated pipeline to classify Zotero library items into subject collections using Claude AI,
writing results back via the Zotero Web API.

## Scripts

| Script | Purpose |
|--------|---------|
| `zotero_classify.py` | Original pipeline. Fetches unfiled items from local Zotero, classifies into 9 old-taxonomy collections using Haiku (single-label, threshold 0.85), writes ambiguous items to CSV. |
| `reclassify_economics.py` | Re-classifies items from an ambiguous CSV against an updated category list. Pass a CSV path as argument. |
| `hybrid_classify.py` | **Current main script.** Classifies all library items into the 14-category new taxonomy. Hybrid model strategy: Sonnet (with abstract, threshold 0.70) + Haiku (no abstract, threshold 0.50). Multi-label: up to 3 collections per item. Non-destructive: new assignments are added on top of existing collections. |

## Running

```bash
# Full live run — new taxonomy, all items
python hybrid_classify.py

# Dry run / test
python hybrid_classify.py --dry-run --limit 50

# Original 9-collection pipeline (old taxonomy)
python zotero_classify.py --dry-run --limit 50

# Re-classify an ambiguous CSV for a new category
python reclassify_economics.py ambiguous_items_YYYYMMDD_HHMMSS.csv [--dry-run]
```

API keys are loaded automatically from `.claude/settings.local.json` — no export needed.

## Zotero Setup

- **Local API** (reads): `http://localhost:23119/api` — Zotero must be running.
  - NOTE: `collectionID=unfiled` does not filter correctly via the local API. Use the Web API to determine true unfiled status.
- **Web API** (reads + writes): `https://api.zotero.org/users/11346380/...`
- **User ID**: `11346380` (lcgillies)
- **True unfiled count**: only 19 items had no collection at all as of 2026-05-25 (the rest had been assigned by earlier runs).

## Taxonomy — Original 9 Collections (old schema, still in Zotero)

| Name | Key |
|------|-----|
| 20th-Century Political History | 7GG9T82M |
| Social, Labour & Gender History | 7NRU88RV |
| Rhetoric, Propaganda & Political Thought | DCR5D8M2 |
| Software | EXEPAUVM |
| Film & Cinema Studies | RNA9UVII |
| British Foreign Policy & Diplomacy | RQS3D3RV |
| Historiography & Philosophy of History | SXJMG7MR |
| Neurology and Perception | Z9ZPTGJK |
| Economics | 4DJVE8EH |

## Taxonomy — New 14-Category Schema (current, used by hybrid_classify.py)

Derived from abstract analysis of 165 items with abstract text. Replaces the old schema for new runs.

| Name | Key | Description |
|------|-----|-------------|
| British History (to 1880) | Z4264QR8 | Tudor, Stuart, Georgian and early Victorian politics, intellectual life, culture and foreign policy |
| British History (1880 to present) | EKCA2G5F | Late Victorian, Edwardian and 20th–21st century British politics, society, diplomacy and ideas |
| European & World History (1880-1990) | 3A3WZX54 | Non-British political and social history 1880–1990, including Nazi Germany, the Holocaust, fascism and the Cold War |
| Contemporary History & Politics (post-1990) | 45PCH5TS | Global political events, conflicts and political developments since 1990 |
| Historiography & Philosophy of History | SXJMG7MR | Historical methodology, theory of history and philosophy of historical knowledge |
| Critical Theory & Continental Philosophy | W8UZ8JEA | Frankfurt School (Adorno, Benjamin, Kracauer), Western Marxism and continental philosophical traditions |
| Literary Studies & Narrative Theory | 897UHPCN | Literature, literary criticism, narrative form, mimesis and theories of representation |
| Economics & Political Economy | M7S8IW47 | Economic theory, political economy, finance, capitalism and history of economic thought |
| Media, Technology & Visual Culture | NVKBQSUR | Film, photography, broadcasting, digital media, art history and architecture — historical and contemporary |
| Music Theory & Musicology | ZQATTJHG | Musical analysis, composers, music theory and historical musicology |
| Cultural Geography & Urban Studies | XG6C8ESK | Place, landscape, urban experience and the spatial dimensions of culture and memory |
| Social, Labour & Gender History | 7NRU88RV | Social history, labour movements, gender studies and feminist history |
| Neurology & Perception | FGDFE7VA | Neurobiology, neuroanatomy and the science of perception |
| Software & Computing | HQJ4REK8 | Software design, architecture, programming languages and computer science |

## Model Strategy (hybrid_classify.py)

| Condition | Model | Confidence threshold | On failure |
|-----------|-------|---------------------|-----------|
| Item has abstract | claude-sonnet-4-5-20250929 | ≥ 0.70 | → `ambiguous_new_*.csv` |
| No abstract | claude-haiku-4-5-20251001 | ≥ 0.50 | → `other_items_*.csv` |

- Batch sizes: Sonnet 25 items, Haiku 50 items
- `max_tokens = 4096`
- `RATE_PAUSE = 0.6s` between Zotero writes
- Up to 3 collections assigned per item (multi-label), ranked by relevance
- Assignments are non-destructive: new collection keys are merged with existing ones

## State (as of 2026-05-25)

### Run history

| Date | Script | Result |
|------|--------|--------|
| 2026-05-24 | `zotero_classify.py` (old taxonomy) | 4,056 items → 2,734 assigned, 1,321 ambiguous → `ambiguous_items_20260524_190704.csv` |
| 2026-05-24 | `reclassify_economics.py` | 323 items from ambiguous CSV → Economics collection |
| 2026-05-25 | `hybrid_classify.py` (new taxonomy) | 4,061 items → **3,946 assigned**, 7 ambiguous, 97 other, 1 error |

### Output files (2026-05-25 hybrid run)

- `other_items_20260525_152246.csv` — 97 items: no abstract + confidence < 0.50. Mostly stub/incomplete records (bare citation keys, empty titles). Need manual review or deletion.
- `ambiguous_new_20260525_152246.csv` — 7 items: has abstract but Sonnet confidence < 0.70. Worth manual inspection.
- 1 write error: key `Z47NS4WS` (transient 502 Bad Gateway). Can be patched manually or re-run.

### Library coverage

- Total items (excl. attachments): 4,061
- Items with ≥ 1 new-taxonomy collection: 3,946 (97.2%)
- Items with abstract text: ~1,118 (27.6% of library)
- Truly unfiled (no collection at all): 19 as of 2026-05-25

## GitHub

- Repo: `lcgillies/zotero` — `https://github.com/lcgillies/zotero`
- PR #1 open on branch `fix/classification-pipeline` — contains all pipeline bug fixes

## Known Issues / Notes

- `collectionID=unfiled` on the local API (port 23119) returns ALL items, not just unfiled ones. Always use the Web API to check true unfiled status.
- Titles/abstracts containing double-quotes can break the Claude prompt JSON. The `build_message()` function replaces `"` with `'` to mitigate this.
- Items in `other_items_*.csv` are mostly incomplete stub records (e.g. `Takami 2014`) — consider reviewing for deletion rather than re-classification.
- The old 9-collection taxonomy (Film & Cinema Studies, Rhetoric & Propaganda, etc.) is still present in Zotero and still has items assigned to it. The new 14-category taxonomy is layered on top. You may want to eventually retire the old collections.
- Abstract coverage is low (27.6%) — enriching metadata via DOI lookup (e.g. with `pyzotero` + CrossRef API) before re-running could significantly improve classification quality for the remaining 72.4%.
