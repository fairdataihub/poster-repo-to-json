#!/usr/bin/env python3
"""
Post-batch quality check for poster extractions.
Scans all extractions and logs failed ones to a TSV for later re-processing.

Checks:
1. Multiple descriptions (LLM dumped content into descriptions instead of sections)
2. Empty/missing content sections
3. JSON parse errors
4. Other extraction errors

Output: /home/james/corpus_output/failed_extractions.tsv
"""
import json
import pathlib
from datetime import datetime

EXT_DIR = pathlib.Path('/home/james/corpus_output/extractions')
FAILED_TSV = pathlib.Path('/home/james/corpus_output/failed_extractions.tsv')

failures = []

for f in sorted(EXT_DIR.glob('*_extracted.json')):
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, Exception):
        failures.append((f.name, 'corrupt_json', 'File could not be parsed'))
        continue

    if 'error' in data:
        failures.append((f.name, 'extraction_error', data['error'][:100]))
        continue

    # Check: multiple descriptions (LLM failure)
    descs = data.get('descriptions') or []
    if isinstance(descs, list) and len(descs) > 1:
        types = [d.get('descriptionType', '?') if isinstance(d, dict) else type(d).__name__ for d in descs]
        failures.append((f.name, 'multi_description', f'{len(descs)} descriptions: {types}'))
        continue

    # Check: no content sections
    content = data.get('content') or {}
    sections = content.get('sections', []) if isinstance(content, dict) else []
    if not sections:
        failures.append((f.name, 'no_content', 'No content sections extracted'))
        continue

    # Check: empty title
    titles = data.get('titles') or []
    if not titles:
        failures.append((f.name, 'no_title', 'Missing titles list'))
        continue
    first_title = titles[0]
    title_str = first_title.get('title', '') if isinstance(first_title, dict) else str(first_title)
    if not title_str.strip():
        failures.append((f.name, 'no_title', 'Missing or empty title'))
        continue

# Write TSV
with open(FAILED_TSV, 'w') as f:
    f.write('filename\tfailure_type\tdetail\n')
    for name, ftype, detail in failures:
        f.write(f'{name}\t{ftype}\t{detail}\n')

# Summary
from collections import Counter
type_counts = Counter(t for _, t, _ in failures)
total_files = len(list(EXT_DIR.glob('*_extracted.json')))
good = total_files - len(failures)

print(f"[{datetime.now().isoformat()}] QC: {total_files} total, {good} good, {len(failures)} failed")
for ftype, count in type_counts.most_common():
    print(f"  {ftype}: {count}")
print(f"Failed list: {FAILED_TSV}")
