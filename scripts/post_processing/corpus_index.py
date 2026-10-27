"""Find a record's source files without listing a corpus directory once per record.

A corpus holds about 24,000 extraction files in one directory; a glob per record lists it
every time, which made a pass over the corpus take hours on a busy disk. The directory is
listed once and indexed by (source, record id).
"""
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def _extraction_index(extractions_dir):
    """(src, record id) -> sorted extraction file names ('{src}_{id}_..._extracted.json')."""
    idx = {}
    try:
        names = os.listdir(extractions_dir)
    except OSError:
        return {}
    for n in names:
        if not n.endswith("_extracted.json"):
            continue
        src, _, rest = n.partition("_")
        rid, sep, _ = rest.partition("_")
        if sep:
            idx.setdefault((src, rid), []).append(n)
    return {k: sorted(v) for k, v in idx.items()}


def extraction_files_in(extractions_dir, src, rid):
    """Paths of one record's poster2json extraction files in `extractions_dir`, sorted:
    what glob('{extractions_dir}/{src}_{rid}_*_extracted.json') returns."""
    d = str(extractions_dir)
    return [os.path.join(d, n) for n in _extraction_index(d).get((src, str(rid)), [])]


def extraction_files(corpus, src, rid):
    """extraction_files_in() for the corpus's extractions/ directory."""
    return extraction_files_in(Path(corpus) / "extractions", src, rid)
