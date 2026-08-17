"""Bibliography source: a collection from the local Zotero API.

Reads metadata and attached PDF paths from a running Zotero (7+) instance
through its local HTTP API via pyzotero (`local=True`). Better BibTeX is
not required. Read-only; the only endpoint touched is localhost.

Pagination pitfall: pyzotero listing calls return only the first page
(~25 items) by default. Every listing call here is wrapped in
`zot.everything(...)`. Never call a listing method bare.

Nested collections are addressed with a `/`-separated path
("PhD/Chapter 3"); a bare name must be unambiguous across the library.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pyzotero import zotero as _pyzotero

from citefact.models import Source


def _connect() -> Any:
    # Library id "0" means "current user" on the local API.
    return _pyzotero.Zotero(library_id="0", library_type="user", local=True)


def _resolve_collection_key(zot: Any, name: str) -> str:
    collections = zot.everything(zot.collections())
    by_key = {c["key"]: c for c in collections}

    def children_of(parent_key: str | bool, wanted: str) -> list[dict]:
        return [
            c for c in collections
            if c["data"]["name"] == wanted
            and (c["data"].get("parentCollection") or False) == parent_key
        ]

    segments = name.split("/")
    if len(segments) > 1:
        # Walk the path from the top level down.
        parent: str | bool = False
        node: dict | None = None
        for segment in segments:
            matches = children_of(parent, segment)
            # A path segment's parent may itself be nested anywhere, so on
            # the first segment accept any collection with that name too.
            if not matches and parent is False:
                matches = [c for c in collections if c["data"]["name"] == segment]
            if not matches:
                raise ValueError(
                    f"Zotero collection path {name!r} not found at segment "
                    f"{segment!r}."
                )
            if len(matches) > 1:
                raise ValueError(f"Ambiguous collection path segment {segment!r}.")
            node = matches[0]
            parent = node["key"]
        assert node is not None
        return node["key"]

    matches = [c for c in collections if c["data"]["name"] == name]
    if not matches:
        available = ", ".join(sorted(c["data"]["name"] for c in collections))
        raise ValueError(
            f"Zotero collection {name!r} not found. Available: {available}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous collection name {name!r} ({len(matches)} matches); "
            "use a nested path like \"Parent/Child\"."
        )
    return matches[0]["key"]


def _authors_string(creators: list[dict]) -> str:
    preferred = [c for c in creators if c.get("creatorType") == "author"] or creators
    parts: list[str] = []
    for c in preferred:
        last = c.get("lastName", "")
        first = c.get("firstName", "")
        single = c.get("name", "")  # institutional author
        if last:
            parts.append(f"{last}, {first}" if first else last)
        elif single:
            parts.append(single)
    return " and ".join(parts)


def _year_of(date: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", date or "")
    return int(match.group(0)) if match else None


def _source_key(authors: str, year: int | None, taken: set[str]) -> str:
    surname = authors.split(",", 1)[0].split(" and ")[0]
    base = re.sub(r"\W", "", surname).lower() or "anon"
    key = f"{base}{year if year is not None else ''}"
    candidate = key
    suffix = ord("a")
    while candidate in taken:
        candidate = f"{key}{chr(suffix)}"
        suffix += 1
    return candidate


def _pdf_path(zot: Any, item_key: str) -> Path | None:
    for child in zot.everything(zot.children(item_key)):
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        if data.get("contentType") != "application/pdf":
            continue
        href = child.get("links", {}).get("enclosure", {}).get("href", "")
        if href.startswith("file://"):
            path = Path(unquote(urlparse(href).path))
        elif data.get("path", "").startswith("/"):
            path = Path(data["path"])
        else:
            continue
        if path.exists():
            return path
    return None


def load_zotero_collection(name: str) -> dict[str, Source]:
    """Load a Zotero collection as bibliography sources with PDF paths.

    Raises RuntimeError when the local API is unreachable (Zotero not
    running) and ValueError for unknown/ambiguous collection names.
    """
    zot = _connect()
    try:
        collection_key = _resolve_collection_key(zot, name)
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Could not reach the local Zotero API at localhost:23119. "
            f"Is Zotero (7 or later) running? ({exc})"
        ) from exc

    items = zot.everything(zot.collection_items_top(collection_key))
    sources: dict[str, Source] = {}
    for item in items:
        data = item.get("data", {})
        if data.get("itemType") in ("attachment", "note", "annotation"):
            continue
        title = (data.get("title") or "").strip()
        authors = _authors_string(data.get("creators", []))
        if not title or not authors:
            continue
        year = _year_of(data.get("date", ""))
        key = _source_key(authors, year, set(sources))
        sources[key] = Source(
            id=key,
            title=title,
            authors=authors,
            year=year,
            pdf_path=_pdf_path(zot, item["key"]),
        )
    return sources
