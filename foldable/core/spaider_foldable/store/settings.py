"""User settings persisted in the ``settings`` table (JSON per key)."""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..db import Database


class UserSettings(BaseModel):
    thinking_enabled: bool = False
    context_tokens: int = Field(default=8192, ge=2048, le=131072)
    max_answer_tokens: int = Field(default=1024, ge=64, le=16384)
    memory_auto_extract: bool = True
    embedding_enabled: bool = True
    threads: Optional[int] = Field(default=None, ge=1, le=256)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    system_prompt_extra: str = Field(default="", max_length=4000)
    performance_mode: Literal["auto", "quiet", "fast"] = "auto"
    gpu_mode: Literal["auto", "off"] = "auto"


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self) -> UserSettings:
        raw = {r["key"]: json.loads(r["value_json"]) for r in self.db.query("SELECT key, value_json FROM settings")}
        try:
            return UserSettings.model_validate({k: v for k, v in raw.items() if k in UserSettings.model_fields})
        except ValidationError:
            return UserSettings()

    def update(self, patch: dict[str, Any]) -> UserSettings:
        current = self.load().model_dump()
        unknown = set(patch) - set(UserSettings.model_fields)
        if unknown:
            raise ValueError(f"unbekannte Einstellung: {', '.join(sorted(unknown))}")
        current.update(patch)
        validated = UserSettings.model_validate(current)  # raises ValidationError
        with self.db.transaction() as conn:
            for k, v in validated.model_dump().items():
                conn.execute(
                    "INSERT INTO settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",  # noqa: E501
                    (k, json.dumps(v)),
                )
        return validated
