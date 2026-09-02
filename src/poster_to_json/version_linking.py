#!/usr/bin/env python3
"""
Version linking - collapse and link auto-indexed poster versions.

A repository can hold the same poster more than once as an explicit version
family: Zenodo groups versions under a concept record, Figshare keeps one
article id and mints a ``.vN`` DOI per version. Auto-indexing harvests each
version as its own record, so without linking the corpus shows them as
unrelated posters.

We keep every version. This module works out which records belong to the same
family, what position each holds, and which one is current, then writes that
onto the poster JSON in two forms:

1. ``relatedIdentifiers`` entries using the DataCite version relations
   (IsVersionOf, IsNewVersionOf, IsPreviousVersionOf), which any DataCite
   consumer already understands.
2. A ``versionInfo`` object carrying the same facts as plain scalars, so the
   platform can populate its versionRoot / versionSequence / isLatestVersion
   columns without re-deriving anything.

Two properties of the upstream data drive the design:

* Sequence comes from the repository, never from our local ordering. Zenodo
  reports a 0-based ``index`` across the whole family; our harvest may hold
  only some of it. Concept 10572542, for example, gives us records at index 1
  and index 2 while index 0 was never harvested. Renumbering those locally to
  1 and 2 would assert that the second version is the original.
* "Latest" comes from the repository too. Zenodo's ``is_last`` tells us a newer
  version exists even when we have not harvested it, which no amount of
  comparing harvested siblings can reveal.

Scope: this handles repository-declared version families only. Posters deposited
twice under separate DOIs, or cross-posted to both Zenodo and Figshare, are not
versions in the repository's sense and are not touched here.
"""

import logging
import re
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# A Figshare version DOI: 10.6084/m9.figshare.21359946.v1
_FIGSHARE_VERSION_DOI_RE = re.compile(r"^(?P<base>10\.\d{4,9}/.+?)\.v(?P<n>\d+)$", re.I)

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
        root: Stable family identifier. The Zenodo concept DOI when minted,
            otherwise a repository-scoped key such as ``zenodo:recid:10572542``
            or ``figshare:article:21359946``.
        root_type: ``DOI`` when root is a concept DOI, else ``Other``.
        sequence: 1-based position as the repository counts it.
        is_latest: True when the repository says no newer version exists.
        own_doi: This record's own DOI, used to cross-link siblings.
        source: Which signal produced the family, for provenance and QA.
    """

    __slots__ = ("root", "root_type", "sequence", "is_latest", "own_doi", "source")

    def __init__(self, root, root_type, sequence, is_latest, own_doi, source):
        self.root = root
        self.root_type = root_type
        self.sequence = sequence
        self.is_latest = is_latest
        self.own_doi = own_doi
        self.source = source

    def to_dict(self) -> Dict:
        info = {
            "versionRoot": self.root,
            "versionRootType": self.root_type,
            "versionSequence": self.sequence,
            "isLatestVersion": self.is_latest,
            "versionSource": self.source,
        }
        return info

    def __repr__(self):  # pragma: no cover - debugging aid
        return (
            f"VersionFamily(root={self.root!r}, seq={self.sequence}, "
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
    sequence we store is ``index + 1`` and stays stable across re-harvests even
    when earlier versions were never indexed.
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
        # No usable version graph. Zenodo omits relations on some older
        # records; treat the record as the sole version of its concept.
        sequence = 1
        is_latest = True
        source = "zenodo-concept"
    else:
        sequence = index + 1
        is_last = entry.get("is_last")
        is_latest = True if is_last is None else bool(is_last)
        source = "zenodo-relations"

    if concept_doi:
        root, root_type = concept_doi, "DOI"
    else:
        root, root_type = f"zenodo:recid:{concept_recid}", "Other"

    return VersionFamily(
        root=root,
        root_type=root_type,
        sequence=sequence,
        is_latest=is_latest,
        own_doi=_normalize_doi(record.get("doi") or metadata.get("doi")),
        source=source,
    )


def from_figshare(record: Dict) -> Optional[VersionFamily]:
    """Read the version family out of a raw Figshare article.

    Figshare keeps a stable article ``id`` across versions and mints a DOI per
    version ending in ``.vN``; ``version`` is a 1-based integer. There is no
    concept DOI, so the family key is the article id. Figshare has no
    equivalent of Zenodo's ``is_last``, so latest is settled later by
    :func:`link_families` across the harvested siblings.
    """
    if not isinstance(record, dict):
        return None

    own_doi = _normalize_doi(record.get("doi"))
    article_id = _clean(record.get("id"))

    version = record.get("version")
    sequence = None
    source = "figshare-version"
    if isinstance(version, bool):
        version = None
    if isinstance(version, int) and version > 0:
        sequence = version
    elif isinstance(version, str) and version.strip().isdigit():
        sequence = int(version.strip())

    base_doi = ""
    match = _FIGSHARE_VERSION_DOI_RE.match(own_doi)
    if match:
        base_doi = match.group("base")
        if sequence is None:
            sequence = int(match.group("n"))
            source = "figshare-doi"

    if sequence is None:
        sequence = 1
        source = "figshare-default"

    if article_id:
        root, root_type = f"figshare:article:{article_id}", "Other"
    elif base_doi:
        root, root_type = base_doi, "DOI"
    else:
        return None

    return VersionFamily(
        root=root,
        root_type=root_type,
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


def _strip_version_relations(poster_json: Dict) -> List[Dict]:
    """Drop version relations we previously wrote, keep depositor-declared ones.

    Rewriting must be idempotent: linking a corpus twice, or relinking after a
    re-harvest, has to converge rather than accumulate stale sibling pointers.
    """
    existing = poster_json.get("relatedIdentifiers")
    if not isinstance(existing, list):
        return []
    return [
        r
        for r in existing
        if not (isinstance(r, dict) and r.get("relationType") in _VERSION_RELATIONS)
    ]


def apply_version_info(
    poster_json: Dict,
    family: VersionFamily,
    previous_doi: Optional[str] = None,
    next_doi: Optional[str] = None,
    family_size: int = 1,
) -> Dict:
    """Write version linking onto ``poster_json`` in place and return it.

    A record that is the only known member of its family and sits at sequence 1
    with nothing newer upstream gets no annotation, because there is nothing to
    link. Every other record gets both the DataCite relations and
    ``versionInfo``.
    """
    # The repository's own free-text version string, if the converter set one.
    # Zenodo lets depositors write anything there (dates are common), so it
    # cannot drive ordering, but it is the depositor's statement and is kept.
    # Once we have linked a record, poster_json["version"] holds our own
    # sequence, so the depositor's string can only be read back out of
    # versionInfo. Re-reading the field would capture our output as if it were
    # theirs.
    previous_info = poster_json.get("versionInfo")
    if isinstance(previous_info, dict):
        repository_version = previous_info.get("repositoryVersion")
    else:
        repository_version = _clean(poster_json.get("version")) or None

    solitary = (
        family_size <= 1
        and family.sequence == 1
        and family.is_latest
        and not previous_doi
        and not next_doi
    )
    if solitary:
        remaining = _strip_version_relations(poster_json)
        if remaining:
            poster_json["relatedIdentifiers"] = remaining
        else:
            poster_json.pop("relatedIdentifiers", None)
        if isinstance(previous_info, dict):
            # Undo an earlier linking run: restore the depositor's own string
            # rather than leaving a sequence number from a family this record
            # no longer belongs to.
            poster_json.pop("versionInfo", None)
            if repository_version:
                poster_json["version"] = repository_version
            else:
                poster_json.pop("version", None)
        return poster_json

    relations = _strip_version_relations(poster_json)
    seen = {
        (r.get("relatedIdentifier", "").lower(), r.get("relationType"))
        for r in relations
        if isinstance(r, dict)
    }

    def add(identifier: str, id_type: str, relation: str):
        ident = _clean(identifier)
        if not ident:
            return
        key = (ident.lower(), relation)
        if key in seen:
            return
        seen.add(key)
        relations.append(
            {
                "relatedIdentifier": ident,
                "relatedIdentifierType": id_type,
                "relationType": relation,
                "resourceTypeGeneral": "Text",
            }
        )

    # The family anchor. Only emitted as a DataCite relation when the root is a
    # real resolvable DOI; our synthetic repository keys are not identifiers
    # anyone can resolve and belong in versionInfo only.
    if family.root_type == "DOI":
        add(family.root, "DOI", REL_IS_VERSION_OF)

    if previous_doi:
        add(previous_doi, "DOI", REL_IS_NEW_VERSION_OF)
    if next_doi:
        add(next_doi, "DOI", REL_IS_PREVIOUS_VERSION_OF)

    if relations:
        poster_json["relatedIdentifiers"] = relations
    else:
        poster_json.pop("relatedIdentifiers", None)

    info = family.to_dict()
    info["versionCount"] = family_size
    if repository_version:
        info["repositoryVersion"] = repository_version
    poster_json["versionInfo"] = info

    # DataCite's own version field. The repository's free-text version string
    # is unreliable for ordering (Zenodo lets depositors put anything in it,
    # commonly a date), so we publish the positional sequence here and keep the
    # depositor's string in versionInfo.repositoryVersion.
    poster_json["version"] = str(family.sequence)

    return poster_json


def link_families(items: Sequence[Dict]) -> Dict[str, int]:
    """Link a whole corpus of harvested records in place.

    Each item is ``{"family": VersionFamily, "poster_json": dict}``. Records are
    grouped by family root, ordered by repository sequence, and cross-linked to
    their harvested neighbours. Returns counts for logging.

    Neighbour links point at the versions we actually hold. Where the harvest
    has a gap, the neighbour link skips it rather than inventing a DOI, while
    ``versionSequence`` still reflects the repository's true numbering.
    """
    groups: Dict[str, List[Dict]] = {}
    stats = {"records": 0, "families": 0, "multi_version_families": 0, "linked": 0}

    for item in items:
        family = item.get("family")
        if not isinstance(family, VersionFamily):
            continue
        stats["records"] += 1
        groups.setdefault(family.root, []).append(item)

    stats["families"] = len(groups)

    for root, members in groups.items():
        members.sort(key=lambda m: (m["family"].sequence, m["family"].own_doi))
        size = len(members)
        if size > 1:
            stats["multi_version_families"] += 1

        # Figshare gives no upstream "latest" flag, so the highest harvested
        # version is the best available answer. Zenodo's is_last is authoritative
        # and left alone.
        figshare_only = all(
            m["family"].source.startswith("figshare") for m in members
        )
        if figshare_only:
            for i, m in enumerate(members):
                m["family"].is_latest = i == size - 1

        for i, member in enumerate(members):
            previous_doi = members[i - 1]["family"].own_doi if i > 0 else None
            next_doi = members[i + 1]["family"].own_doi if i < size - 1 else None
            before = member["poster_json"].get("versionInfo")
            apply_version_info(
                member["poster_json"],
                member["family"],
                previous_doi=previous_doi,
                next_doi=next_doi,
                family_size=size,
            )
            if member["poster_json"].get("versionInfo") != before:
                stats["linked"] += 1

    logger.info(
        "version linking: %d records, %d families, %d with multiple versions, %d annotated",
        stats["records"],
        stats["families"],
        stats["multi_version_families"],
        stats["linked"],
    )
    return stats
