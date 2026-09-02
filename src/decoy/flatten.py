"""Flatten/unflatten nested dict records (e.g. Elasticsearch `_source`)
into flat dotted-path dicts so record_masker.py can treat every leaf
value uniformly, then restore the original nesting afterward.

List items are addressed with a `[i]` suffix on the path segment, e.g.
`user.emails[0]` for `{"user": {"emails": ["a@x.com"]}}`.
"""

from __future__ import annotations

import re
from typing import Any

_INDEXED_SEGMENT_RE = re.compile(r"^(.*)\[(\d+)\]$")


def flatten_record(record: dict, parent_key: str = "", sep: str = ".") -> dict[str, Any]:
    items: dict[str, Any] = {}
    for key, value in record.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else str(key)
        if isinstance(value, dict):
            items.update(flatten_record(value, new_key, sep))
        elif isinstance(value, list):
            for i, element in enumerate(value):
                indexed_key = f"{new_key}[{i}]"
                if isinstance(element, dict):
                    items.update(flatten_record(element, indexed_key, sep))
                else:
                    items[indexed_key] = element
        else:
            items[new_key] = value
    return items


def unflatten_record(flat: dict[str, Any], sep: str = ".") -> dict:
    root: dict = {}
    for compound_key, value in flat.items():
        segments = compound_key.split(sep)
        cursor = root
        for i, segment in enumerate(segments):
            is_last = i == len(segments) - 1
            match = _INDEXED_SEGMENT_RE.match(segment)
            if match:
                name, index_str = match.group(1), int(match.group(2))
                container = cursor.setdefault(name, [])
                while len(container) <= index_str:
                    container.append({} if not is_last else None)
                if is_last:
                    container[index_str] = value
                else:
                    if not isinstance(container[index_str], dict):
                        container[index_str] = {}
                    cursor = container[index_str]
            else:
                if is_last:
                    cursor[segment] = value
                else:
                    cursor = cursor.setdefault(segment, {})
    return root
