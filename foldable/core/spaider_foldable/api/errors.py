from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, **extra: Any):
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra
        super().__init__(detail)

    def body(self) -> dict:
        return {"detail": self.detail, "code": self.code, **self.extra}
