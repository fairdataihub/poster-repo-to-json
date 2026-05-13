#!/bin/bash
source /home/james/poster_env/bin/activate

echo "=== File counts ==="
echo -n "Zenodo merged: "
find /home/james/corpus_output/merged/zenodo -name '*.json' 2>/dev/null | wc -l
echo -n "Figshare merged: "
find /home/james/corpus_output/merged/figshare -name '*.json' 2>/dev/null | wc -l
echo -n "Extraction-only: "
find /home/james/corpus_output/merged/extraction_only -name '*.json' 2>/dev/null | wc -l

echo ""
echo "=== Completeness check: required fields ==="
python3 -c "
import json, os
from pathlib import Path
from collections import Counter

merged = Path('/home/james/corpus_output/merged')
total = 0
missing = Counter()
has_error = 0
no_schema = 0
no_domain = 0
no_lang = 0
no_creators = 0
no_content = 0
has_ror = 0
has_orcid = 0
domain_dist = Counter()

for f in sorted(merged.rglob('*.json')):
    total += 1
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        missing['unreadable'] += 1
        continue

    if 'error' in d:
        has_error += 1
        continue

    if not d.get('\$schema'):
        no_schema += 1
    if not d.get('researchField'):
        no_domain += 1
    if not d.get('language'):
        no_lang += 1
    if not d.get('creators'):
        no_creators += 1
    if not d.get('content'):
        no_content += 1

    rf = d.get('researchField', 'None')
    domain_dist[rf] += 1

    for c in d.get('creators', []):
        if not isinstance(c, dict):
            continue
        for aff in c.get('affiliation') or []:
            if isinstance(aff, dict) and aff.get('affiliationIdentifier'):
                has_ror += 1
                break
        for ni in c.get('nameIdentifiers') or []:
            if isinstance(ni, dict) and ni.get('nameIdentifierScheme') == 'ORCID':
                has_orcid += 1
                break

print(f'Total files: {total}')
print(f'Error files: {has_error}')
print(f'Missing schema: {no_schema}')
print(f'Missing researchField: {no_domain}')
print(f'Missing language: {no_lang}')
print(f'Missing creators: {no_creators}')
print(f'Missing content: {no_content}')
print()
print('Domain distribution:')
for d, c in domain_dist.most_common():
    print(f'  {d}: {c}')
print()
print(f'Creators with ROR: {has_ror}')
print(f'Creators with ORCID: {has_orcid}')
"

echo ""
echo "=== Spot check: 10 random files ==="
find /home/james/corpus_output/merged -name '*.json' 2>/dev/null | shuf | head -10 | while read f; do
    python3 -c "
import json
d = json.load(open('$f'))
name = '$f'.split('/')[-1]
rf = d.get('researchField', '-')
lang = d.get('language', '-')
schema = 'v0.2' if 'v0.2' in d.get('\$schema', '') else 'other'
n_sec = len(d.get('content', {}).get('sections', [])) if isinstance(d.get('content'), dict) else 0
print(f'  {name}: field={rf}, lang={lang}, schema={schema}, sections={n_sec}')
" 2>/dev/null
done
