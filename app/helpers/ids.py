from __future__ import annotations

import secrets
import uuid


def new_id(prefix: str) -> str:
    """Readable, sortable-enough ids: `doc_3f9a…`."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
