from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    return str(uuid.uuid4())


class NotFound(LookupError):
    pass
