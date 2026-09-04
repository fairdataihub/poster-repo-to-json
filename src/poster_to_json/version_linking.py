#!/usr/bin/env python3
"""
Version linking - link auto-indexed poster versions using DataCite relations.

A repository can hold the same poster more than once as an explicit version
family: Zenodo groups versions under a concept record, Figshare keeps one
article id and mints a ``.vN`` DOI per version. Auto-indexing harvests each
version as its own record, so without linking the corpus shows them as
unrelated posters.

We keep every version. This module works out which records belong to the same
family and writes that using fields the poster schema already has:

* ``IsVersionOf`` points at the family's stable DOI. Zenodo publishes this as
  the concept DOI; Figshare's equivalent is the article DOI with the ``.vN``
  suffix removed, which is itself a registered, findable DataCite DOI.
* ``IsNewVersionOf`` and ``IsPreviousVersionOf`` point at the harvested
  siblings, older and newer respectively.

Nothing else is written. In particular the top-level ``version`` field is left
alone: it is the depositor's own statement, Zenodo lets them put anything in it
(dates are common, and one record in our corpus reads "Posters.science
automated"), and overwriting it would destroy real metadata.

The platform reads the three columns it needs straight off the relations:

    versionRootId    <- the IsVersionOf target
    isLatestVersion  <- true when no IsPreviousVersionOf is present
    versionSequence  <- position in the IsNewVersionOf / IsPreviousVersionOf chain

Two properties of the upstream data drive the rest of the design:

* Ordering comes from the repository, never from our local view. Zenodo reports
  a 0-based ``index`` across the whole family, and our harvest may hold only
  part of it. We use that index to order siblings correctly even when earlier
  versions were never indexed.
* "Latest" comes from the repository too, but only as of harvest time. Zenodo
  sets ``is_last`` on whichever record was newest when we fetched it, so a 2023
  record still claims to be latest in a corpus that has since picked up its 2025
  successor. Holding a higher sequence in the same family is proof the flag went
  stale.

Scope: this handles repository-declared version families only. Posters deposited
twice under separate DOIs, or cross-posted to both Zenodo and Figshare, are not
versions in the repository's sense and are not touched here. See
docs/DUPLICATE_LINKING_PROPOSAL.md.
"""

import logging
import re
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# A versioned Figshare DOI: 10.6084/m9.figshare.21359946.v1, and institutional
# variants such as 10.17044/scilifelab.24049458.v2. Stripping the suffix yields
# the article DOI, which is registered and resolves to the latest version.
_VERSION_DOI_SUFFIX_RE = re.compile(r"^(?P<base>10\.\d{4,9}/.+?)\.v(?P<n>\d+)$", re.I)

# DataCite relations we emit. Direction follows the DataCite schema:
#   A IsNewVersionOf B      -> B is older than A
#   A IsPreviousVersionOf B -> B is newer than A
#   A IsVersionOf B         -> B is the concept/family record
REL_IS_VERSION_OF = "IsVersionOf"
REL_IS_NEW_VERSION_OF = "IsNewVersionOf"
REL_IS_PREVIOUS_VERSION_OF = "IsPreviousVersionOf"

_VERSION_RELATIONS = frozenset(
    {REL_IS_VERSION_OF, REL_IS_NEW_VERSION_OF, REL_IS_PREVIOUS_VERSION_OF}
)


class VersionFamily:
    """One record's place in a repository version family.

    Attributes:
        root_doi: The family's stable DOI, resolvable and safe to publish as an
            ``IsVersionOf`` target. Empty when the repository does not mint one.
        group_key: Always populated. Equals ``root_doi`` when there is one, else
            a repository-scoped key such as ``figshare:article:21359946``. Used
            only to group records in memory and never written to a poster.
        sequence: 1-based position as the repository counts it. Used to order
            siblings and to detect stale latest flags. Not published.
        is_latest: True when no newer version is known. Expressed in the output
            by the absence of an ``IsPreviousVersionOf`` relation.
        own_doi: This record's own DOI, used to cross-link siblings.
        source: Which signal produced the family, for logging and QA.
    """

    __slots__ = ("root_doi", "group_key", "sequence", "is_latest", "own_doi", "source")

    def __init__(self, root_doi, group_key, sequence, is_latest, own_doi, source):
        self.root_doi = root_doi
        self.group_key = group_key
        self.sequence = sequence
        self.is_latest = is_latest
        self.own_doi = own_doi
        self.source = source

    def __repr__(self):  # pragma: no cover - debugging aid
        return (
            f"VersionFamily(key={self.group_key!r}, seq={self.sequence}, "
            f"latest={self.is_latest}, source={self.source!r})"
        )


def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_doi(doi) -> str:
    """Strip a DOI down to the bare 10.x/suffix form, lowercased."""
    d = _clean(doi)
    if not d:
        return ""
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:", "", d, flags=re.I)
    return d.strip().lower()


def from_zenodo(record: Dict) -> Optional[VersionFamily]:
    """Read the version family out of a raw Zenodo record.

    Zenodo exposes it as::

        {"conceptrecid": "10572542",
         "conceptdoi": "10.5281/zenodo.10572542",
         "metadata": {"relations": {"version": [
             {"index": 2, "is_last": true,
              "parent": {"pid_type": "recid", "pid_value": "10572542"}}]}}}

    ``index`` is 0-based and counts the whole family as Zenodo knows it, so the
    sequence is ``index + 1`` and stays meaningful across re-harvests even when
    earlier versions were never indexed.
    """
    if not isinstance(record, dict):
        return None

    metadata = record.get("metadata") or {}
    relations = metadata.get("relations") or record.get("relations") or {}
    entries = relations.get("version") if isinstance(relations, dict) else None
    entry = entries[0] if isinstance(entries, list) and entries else {}
    if not isinstance(entry, dict):
        entry = {}

    concept_doi = _normalize_doi(record.get("conceptdoi") or metadata.get("conceptdoi"))
    parent = entry.get("parent") or {}
    concept_recid = _clean(
        record.get("conceptrecid")
        or metadata.get("conceptrecid")
        or (parent.get("pid_value") if isinstance(parent, dict) else "")
    )

    if not concept_doi and not concept_recid:
        return None

    index = entry.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        # No usable version graph. Zenodo omits relations on some older records;
        # treat the record as the sole version of its concept.
        sequence, is_latest, source = 1, True, "zenodo-concept"
    else:
        sequence = index + 1
        is_last = entry.get("is_last")
        is_latest = True if is_last is None else bool(is_last)
        source = "zenodo-relations"

    return VersionFamily(
        root_doi=concept_doi,
        group_key=concept_doi or f"zenodo:recid:{concept_recid}",
        sequence=sequence,
        is_latest=is_latest,
        own_doi=_normalize_doi(record.get("doi") or metadata.get("doi")),
        source=source,
    )


def from_figshare(record: Dict) -> Optional[VersionFamily]:
    """Read the version family out of a raw Figshare article.

    Figshare keeps a stable article ``id`` across versions and mints a DOI per
    version ending in ``.vN``; ``version`` is a 1-based integer. There is no
    concept DOI, but the article DOI with the suffix stripped is registered with
    DataCite and resolves, so it serves the same purpose.

    Figshare publishes no equivalent of Zenodo's ``is_last``, so latest is
    settled later by :func:`link_families` across the harvested siblings.
    """
    if not isinstance(record, dict):
        return None

    own_doi = _normalize_doi(record.get("doi"))
    article_id = _clean(record.get("id"))

    version = record.get("version")
    if isinstance(version, bool):
        version = None
    sequence = None
    source = "figshare-version"
    if isinstance(version, int) and version > 0:
        sequence = version
    elif isinstance(version, str) and version.strip().isdigit():
        sequence = int(version.strip())

    base_doi = ""
    match = _VERSION_DOI_SUFFIX_RE.match(own_doi)
    if match:
        base_doi = match.group("base")
        if sequence is None:
            sequence = int(match.group("n"))
            source = "figshare-doi"

    if sequence is None:
        sequence, source = 1, "figshare-default"

    group_key = base_doi or (f"figshare:article:{article_id}" if article_id else "")
    if not group_key:
        return None

    return VersionFamily(
        root_doi=base_doi,
        group_key=group_key,
        sequence=sequence,
        # Provisional. link_families settles this against harvested siblings.
        is_latest=True,
        own_doi=own_doi,
        source=source,
    )


def from_record(record: Dict, source: str) -> Optional[VersionFamily]:
    """Dispatch to the reader for ``source`` (``zenodo`` or ``figshare``)."""
    key = _clean(source).lower()
    if key.startswith("zenodo"):
        return from_zenodo(record)
    if key.startswith("figshare"):
        return from_figshare(record)
    return None


def _strip_version_relations(poster_json: Dict, ours: Sequence[str]) -> List[Dict]:
    """Return relatedIdentifiers minus the version relations we manage.

    Rewriting must be idempotent: relinking after a re-harvest has to converge
    rather than accumulate stale sibling pointers. But a version relation in a
    record is not necessarily one we wrote. Depositors declare their own, and
    the corpus has them: record 12681959 arrived with its own ``IsVersionOf``.
    Stripping every version relation destroyed those.

    So only relations pointing at an identifier in ``ours`` are removed, that
    being the exact set of targets this family can produce: the family DOI and
    the DOIs of its harvested members. Anything pointing elsewhere is the
    depositor's and is kept.
    """
    existing = poster_json.get("relatedIdentifiers")
    if not isinstance(existing, list):
        return []
    managed = {_normalize_doi(o) for o in ours if o}
    return [
        r
        for r in existing
        if not (
            isinstance(r, dict)
            and r.get("relationType") in _VERSION_RELATIONS
            and _normalize_doi(r.get("relatedIdentifier")) in managed
        )
    ]


def apply_version_links(
    poster_json: Dict,
    family: VersionFamily,
    previous_doi: Optional[str] = None,
    next_doi: Optional[str] = None,
    family_size: int = 1,
    family_dois: Sequence[str] = (),
) -> Dict:
    """Write version relations onto ``poster_json`` in place and return it.

    A record that is the only known member of its family, sits at sequence 1 and
    has nothing newer upstream is left untouched, because there is nothing to
    say about it. Everything else gets the DataCite relations.

    ``family_dois`` is every DOI this family could be linked to, used to
    recognise our own previous output without touching the depositor's.
    """
    solitary = (
        family_size <= 1
        and family.sequence == 1
        and family.is_latest
        and not previous_doi
        and not next_doi
    )

    managed = list(family_dois) or [family.root_doi, previous_doi, next_doi]
    original = poster_json.get("relatedIdentifiers")

    if solitary:
        # Nothing to assert, so nothing to clean up. Leaving the record alone is
        # the safe choice: at this point we cannot distinguish a stale link from
        # a previous run from a relation the depositor declared themselves, and
        # deleting the depositor's is the worse error.
        return poster_json

    relations = _strip_version_relations(poster_json, managed)
    had_version_relations = isinstance(original, list) and len(original) != len(relations)
    seen = {
        (_clean(r.get("relatedIdentifier")).lower(), r.get("relationType"))
        for r in relations
        if isinstance(r, dict)
    }

    def add(identifier: str, relation: str):
        ident = _clean(identifier)
        if not ident:
            return
        key = (ident.lower(), relation)
        if key in seen:
            return
        seen.add(key)
        # The related resource is another version of this poster, so it is a
        # Poster. Our schema follows DataCite 4.7, whose resourceType enum
        # includes "Poster"; the pre-4.7 practice of typing a poster as "Text"
        # does not apply here.
        relations.append(
            {
                "relatedIdentifier": ident,
                "relatedIdentifierType": "DOI",
                "relationType": relation,
                "resourceTypeGeneral": "Poster",
            }
        )

    # The family anchor. Emitted only when the repository mints a real DOI for
    # it; our in-memory grouping keys are not identifiers anyone can resolve.
    add(family.root_doi, REL_IS_VERSION_OF)
    add(previous_doi, REL_IS_NEW_VERSION_OF)
    add(next_doi, REL_IS_PREVIOUS_VERSION_OF)

    if relations:
        poster_json["relatedIdentifiers"] = relations
    elif had_version_relations:
        poster_json.pop("relatedIdentifiers", None)

    return poster_json


def link_families(items: Sequence[Dict]) -> Dict[str, int]:
    """Link a whole corpus of harvested records in place.

    Each item is ``{"family": VersionFamily, "poster_json": dict}``. Records are
    grouped by family, ordered by repository sequence, and cross-linked to their
    harvested neighbours. Returns counts for logging.

    Neighbour links point at the versions we actually hold. Where the harvest has
    a gap the link skips it rather than inventing a DOI, so the chain stays
    truthful about what is in the corpus.
    """
    groups: Dict[str, List[Dict]] = {}
    stats = {"records": 0, "families": 0, "multi_version_families": 0, "linked": 0,
             "duplicate_files": 0, "stale_latest_corrected": 0, "no_root_doi": 0}

    for item in items:
        family = item.get("family")
        if not isinstance(family, VersionFamily):
            continue
        stats["records"] += 1
        groups.setdefault(family.group_key, []).append(item)

    stats["families"] = len(groups)

    for members in groups.values():
        # One version can appear as more than one file: the same DOI is present
        # in two corpus slices when a poster was re-ingested in a later harvest.
        # Those files are the same version, not siblings, so they collapse into
        # one slot. Every file in the slot is still annotated; the slot counts
        # once for ordering and neighbours.
        slots: Dict[str, List[Dict]] = {}
        for i, member in enumerate(members):
            doi = member["family"].own_doi
            slots.setdefault(doi if doi else f"\x00no-doi:{i}", []).append(member)
        stats["duplicate_files"] += len(members) - len(slots)

        ordered = sorted(
            slots.values(),
            key=lambda group: (group[0]["family"].sequence, group[0]["family"].own_doi),
        )
        size = len(ordered)
        if size > 1:
            stats["multi_version_families"] += 1

        # A repository's "this is the latest version" flag is only true as of the
        # moment we harvested it. Zenodo sets is_last on the newest record at that
        # time, so a record harvested in 2023 still claims to be latest in a corpus
        # that has since picked up its 2025 successor. Holding a higher sequence in
        # the same family is proof the flag went stale. Figshare publishes no flag
        # and is provisionally set true, which the same rule resolves. Only the
        # highest sequence we hold keeps the repository's own answer.
        highest = ordered[-1][0]["family"].sequence
        for group in ordered:
            family = group[0]["family"]
            if family.sequence < highest and family.is_latest:
                family.is_latest = False
                stats["stale_latest_corrected"] += 1

        # Every DOI this family can be linked to. Used to recognise relations a
        # previous run wrote without disturbing the depositor's own.
        family_dois = [ordered[0][0]["family"].root_doi]
        family_dois += [g[0]["family"].own_doi for g in ordered]

        for i, group in enumerate(ordered):
            family = group[0]["family"]
            previous_doi = ordered[i - 1][0]["family"].own_doi if i > 0 else None
            next_doi = ordered[i + 1][0]["family"].own_doi if i < size - 1 else None
            if not family.root_doi and (size > 1 or family.sequence > 1):
                stats["no_root_doi"] += len(group)
            for member in group:
                member["family"] = family
                before = member["poster_json"].get("relatedIdentifiers")
                before = [dict(r) for r in before] if isinstance(before, list) else None
                apply_version_links(
                    member["poster_json"],
                    family,
                    previous_doi=previous_doi,
                    next_doi=next_doi,
                    family_size=size,
                    family_dois=family_dois,
                )
                if member["poster_json"].get("relatedIdentifiers") != before:
                    stats["linked"] += 1

    logger.info(
        "version linking: %d records, %d families, %d with multiple versions, "
        "%d relinked, %d duplicate files collapsed, %d stale latest flags corrected, "
        "%d without a family DOI",
        stats["records"],
        stats["families"],
        stats["multi_version_families"],
        stats["linked"],
        stats["duplicate_files"],
        stats["stale_latest_corrected"],
        stats["no_root_doi"],
    )
    return stats
