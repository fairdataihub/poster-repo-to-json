"""Delete ALL stale error files that have recoverable cached text.
- CUDA OOM errors: 5,276 (from batch inference disaster)
- JSON parse failed: 3
- Any other recoverable errors
"""
import json, pathlib, collections

EXT_DIR = pathlib.Path('/home/james/corpus_output/extractions')
CACHE_DIR = EXT_DIR / '_raw_text'

removed_by_type = collections.Counter()

for f in list(EXT_DIR.glob('*_extracted.json')):
    try:
        d = json.loads(f.read_text())
    except:
        continue
    if 'error' not in d:
        continue

    stem = f.stem.replace('_extracted', '')
    cache = CACHE_DIR / f'{stem}.json'

    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            if c.get('text') and len(c['text']) > 50:
                err = d['error'][:40]
                removed_by_type[err] += 1
                f.unlink()
        except:
            pass

print("Removed stale error files (all recoverable):")
for err, count in removed_by_type.most_common():
    print(f"  {count:>5}: {err}")
print(f"Total: {sum(removed_by_type.values())}")

# Count what's left
remaining = sum(1 for _ in EXT_DIR.glob('*_extracted.json'))
print(f"\nRemaining extraction files: {remaining}")
