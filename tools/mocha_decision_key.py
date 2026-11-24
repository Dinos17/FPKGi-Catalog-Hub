#!/usr/bin/env python3
"""Canonical cross-language identity for human Mocha review decisions."""
from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone
from typing import Any


def canonical_created_at(value: Any) -> str:
    """Match JavaScript decisionKey(), which converts ISO UTC Z to +00:00."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Decision upload timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Decision upload timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("Decision upload timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def decision_key(platform: Any, category: Any, name: Any, created_at: Any) -> str:
    """Hash the exact identity format also used by netlify/review-functions/review.mjs."""
    if not all(isinstance(value, str) and value.strip()
               for value in (platform, category, name)):
        raise ValueError("Decision identity fields are missing")
    material = "\0".join((
        platform,
        category,
        unicodedata.normalize("NFKC", name).strip().lower(),
        canonical_created_at(created_at),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
