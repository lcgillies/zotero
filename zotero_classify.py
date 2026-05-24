#!/usr/bin/env python3
"""
Zotero Automated Classification Pipeline
----------------------------------------
Fetches unfiled items from Zotero, classifies them into 8 collections
using Claude Haiku, and assigns clear cases automatically.
Ambiguous items are logged to a CSV for manual review.

Requirements:
    pip install anthropic requests

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python zotero_classify.py

    # Dry run (no writes to Zotero):
    python zotero_classify.py --dry-run

    # Process only first N items (for testing):
    python zotero_classify.py --limit 50
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

import anthropic
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ZOTERO_BASE = "http://localhost:23119/api"          # Zotero local HTTP API
ZOTERO_HEADERS = {"Zotero-API-Version": "3"}

COLLECTIONS = {
    "20th-Century Political History":           "7GG9T82M",
    "Social, Labour & Gender History":          "7NRU88RV",
    "Rhetoric, Propaganda & Political Thought": "DCR5D8M2",
    "Software":                                 "EXEPAUVM",
    "Film & Cinema Studies":                    "RNA9UVII",
    "British Foreign Policy & Diplomacy":       "RQS3D3RV",
    "Historiography & Philosophy of History":   "SXJMG7MR",
    "Neurology and Perception":                 "Z9ZPTGJK",
}

CONFIDENCE_THRESHOLD = 0.85   # Below this → ambiguous, goes to review CSV
BATCH_SIZE = 50                # Items per API call to Claude
RATE_LIMIT_PAUSE = 0.25        # Seconds between Zotero write calls

AMBIGUOUS_CSV = f"ambiguous_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------

def zotero_get(path: str, params: dict = None) -> dict | list:
    url = f"{ZOTERO_BASE}{path}"
    r = requests.get(url, headers=ZOTERO_HEADERS, params=params or {})
    r.raise_for_status()
    return r.json()


def zotero_patch(path: str, payload: dict, version: int) -> None:
    url = f"{ZOTERO_BASE}{path}"
    headers = {**ZOTERO_HEADERS, "If-Unmodified-Since-Version": str(version)}
    r = requests.patch(url, headers=headers, json=payload)
    r.raise_for_status()


def fetch_unfiled_items(limit: int | None = None) -> list[dict]:
    """Return all items in /unfiled (no collection assigned), excluding attachments."""
    print("Fetching unfiled items from Zotero...")
    items = []
    start = 0
    page_size = 100
    while True:
        params = {
            "format": "json",
            "itemType": "-attachment",
            "start": start,
            "limit": page_size,
        }
        page = zotero_get("/users/0/items/unfiled", params)
        if not page:
            break
        items.extend(page)
        print(f"  fetched {len(items)} items so far...")
        if len(page) < page_size:
            break
        start += page_size
        if limit and len(items) >= limit:
            items = items[:limit]
            break
    print(f"Total unfiled items to classify: {len(items)}")
    return items


def assign_collection(item_key: str, version: int, collection_key: str, dry_run: bool) -> None:
    """Add the item to the given collection (PATCH, non-destructive)."""
    if dry_run:
        return
    payload = {"collections": [collection_key]}
    zotero_patch(f"/users/0/items/{item_key}", payload, version)
    time.sleep(RATE_LIMIT_PAUSE)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a research librarian classifying academic articles into exactly one of these 8 collections:

1. 20th-Century Political History
2. Social, Labour & Gender History
3. Rhetoric, Propaganda & Political Thought
4. Software
5. Film & Cinema Studies
6. British Foreign Policy & Diplomacy
7. Historiography & Philosophy of History
8. Neurology and Perception

Rules:
- Use ONLY the title, authors, journal/publication name, and year provided.
- Assign each item to exactly ONE collection — the best fit.
- If an item clearly fits one collection, set confidence >= 0.85.
- If genuinely ambiguous between two collections, set confidence < 0.85 and list both candidates.
- Respond ONLY with a JSON array — no preamble, no explanation, no markdown fences.

Output format (one object per input item, same order):
[
  {
    "key": "<item_key>",
    "collection": "<exact collection name from the list above>",
    "confidence": <0.0-1.0>,
    "alternate": "<second-best collection or null>"
  },
  ...
]"""


def build_user_message(batch: list[dict]) -> str:
    lines = []
    for item in batch:
        data = item.get("data", {})
        key = data.get("key", item.get("key", ""))
        title = data.get("title", "").strip()
        authors = ", ".join(
            f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
            for c in data.get("creators", [])
            if c.get("creatorType") == "author"
        ) or "Unknown"
        journal = data.get("publicationTitle") or data.get("bookTitle") or data.get("publisher") or ""
        year = data.get("date", "")[:4] if data.get("date") else ""

        lines.append(
            f'{{"key": "{key}", "title": "{title}", "authors": "{authors}", '
            f'"journal": "{journal}", "year": "{year}"}}'
        )
    return "Classify these items:\n" + "\n".join(lines)


def classify_batch(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    """Send a batch to Claude Haiku and return parsed results."""
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(batch)}],
    )
    raw = message.content[0].text.strip()
    # Strip markdown fences if model adds them despite instructions
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(dry_run: bool, limit: int | None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable not set.")

    client = anthropic.Anthropic(api_key=api_key)
    items = fetch_unfiled_items(limit)

    if not items:
        print("No unfiled items found. Exiting.")
        return

    # Stats
    assigned = 0
    ambiguous_rows = []
    errors = []

    # Reverse lookup: collection name → key
    name_to_key = COLLECTIONS

    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    print(f"\nProcessing {len(batches)} batches of up to {BATCH_SIZE} items each...")
    print(f"Confidence threshold: {CONFIDENCE_THRESHOLD}")
    print(f"Dry run: {dry_run}\n")

    for batch_num, batch in enumerate(batches, 1):
        print(f"Batch {batch_num}/{len(batches)} ({len(batch)} items)...", end=" ", flush=True)
        try:
            results = classify_batch(client, batch)
        except Exception as e:
            print(f"ERROR: {e}")
            for item in batch:
                errors.append(item.get("data", {}).get("key", "?"))
            continue

        # Build a lookup for easy access to item version
        item_by_key = {
            item.get("data", {}).get("key", item.get("key")): item
            for item in batch
        }

        batch_assigned = 0
        batch_ambiguous = 0

        for result in results:
            key = result.get("key")
            collection_name = result.get("collection", "")
            confidence = float(result.get("confidence", 0))
            alternate = result.get("alternate")
            item = item_by_key.get(key)

            if not item:
                continue

            data = item.get("data", {})
            version = item.get("version", 0)
            title = data.get("title", "")

            if confidence >= CONFIDENCE_THRESHOLD and collection_name in name_to_key:
                collection_key = name_to_key[collection_name]
                try:
                    assign_collection(key, version, collection_key, dry_run)
                    assigned += 1
                    batch_assigned += 1
                except Exception as e:
                    errors.append(key)
                    print(f"\n  Write error for {key}: {e}")
            else:
                batch_ambiguous += 1
                ambiguous_rows.append({
                    "key": key,
                    "title": title,
                    "suggested_collection": collection_name,
                    "confidence": f"{confidence:.2f}",
                    "alternate": alternate or "",
                    "authors": ", ".join(
                        f"{c.get('lastName', '')}" for c in data.get("creators", [])
                        if c.get("creatorType") == "author"
                    ),
                    "journal": data.get("publicationTitle", ""),
                    "year": data.get("date", "")[:4],
                })

        print(f"assigned {batch_assigned}, ambiguous {batch_ambiguous}")

    # Write ambiguous CSV
    if ambiguous_rows:
        with open(AMBIGUOUS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "key", "title", "authors", "journal", "year",
                "suggested_collection", "confidence", "alternate"
            ])
            writer.writeheader()
            writer.writerows(ambiguous_rows)
        print(f"\nAmbiguous items written to: {AMBIGUOUS_CSV}")

    # Summary
    print("\n" + "=" * 50)
    print("PIPELINE COMPLETE")
    print(f"  Total items processed : {len(items)}")
    print(f"  Assigned to collection: {assigned}" + (" (dry run — no writes)" if dry_run else ""))
    print(f"  Ambiguous (for review): {len(ambiguous_rows)}")
    print(f"  Errors                : {len(errors)}")
    if errors:
        print(f"  Error keys            : {errors}")
    print("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify unfiled Zotero items with Claude Haiku.")
    parser.add_argument("--dry-run", action="store_true", help="Classify but do not write to Zotero.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N items.")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
