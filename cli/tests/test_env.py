"""Unit tests for ``spaider_cli.lib.env``.

Covers .env parsing, merge, and secret generation. All filesystem ops use
``tmp_path`` — never touches the real ``.env``.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from spaider_cli.lib import env as env_lib


class TestSecretGeneration:
    def test_jwt_secret_is_64_hex(self):
        secret = env_lib.generate_jwt_secret()
        assert len(secret) == 64
        assert re.fullmatch(r"[0-9a-f]+", secret)

    def test_connector_key_decodes_to_32_bytes(self):
        key = env_lib.generate_connector_key()
        decoded = base64.b64decode(key)
        assert len(decoded) == 32

    def test_neo4j_password_nontrivial(self):
        pw = env_lib.generate_neo4j_password()
        assert len(pw) >= 20
        assert pw != env_lib.generate_neo4j_password()  # randomness


class TestParseEnv:
    def test_skips_comments_and_blanks(self):
        text = "# a comment\n\nFOO=bar\n  # indented comment\nBAZ=42\n"
        assert env_lib.parse_env_text(text) == {"FOO": "bar", "BAZ": "42"}

    def test_handles_quotes_verbatim(self):
        text = 'FOO="quoted value"\n'
        parsed = env_lib.parse_env_text(text)
        # Quotes are NOT stripped — matches pydantic-settings runtime behaviour.
        assert parsed["FOO"] == '"quoted value"'

    def test_ignores_non_env_lines(self):
        text = "FOO=bar\nnotanenvline\n123=invalid\n"
        # "123=invalid" starts with a digit so doesn't match the [A-Z_] anchor.
        assert env_lib.parse_env_text(text) == {"FOO": "bar"}


class TestUpdateEnvText:
    def test_overrides_existing_keys_in_place(self):
        template = "# header\nFOO=old\nBAR=keep\n"
        out = env_lib.update_env_text(template, {"FOO": "new"})
        assert "FOO=new" in out
        assert "FOO=old" not in out
        assert "BAR=keep" in out
        assert "# header" in out  # comment preserved

    def test_appends_new_keys_at_end(self):
        template = "FOO=1\n"
        out = env_lib.update_env_text(template, {"FOO": "1", "NEW": "value"})
        # FOO is preserved (still =1), NEW is appended with banner
        assert "FOO=1" in out
        assert "NEW=value" in out
        assert out.index("NEW=value") > out.index("FOO=1")
        assert "added by `spaider init`" in out

    def test_preserves_ordering_and_comments(self):
        template = "# top\nKEY1=a\n# middle comment\nKEY2=b\n"
        out = env_lib.update_env_text(template, {"KEY1": "A", "KEY2": "B"})
        # Order: top, KEY1=A, middle, KEY2=B
        lines = [ln for ln in out.splitlines() if ln]
        assert lines[0] == "# top"
        assert lines[1] == "KEY1=A"
        assert lines[2] == "# middle comment"
        assert lines[3] == "KEY2=B"


class TestWriteEnvFile:
    def test_creates_from_example_when_target_missing(self, tmp_path: Path):
        example = tmp_path / ".env.example"
        example.write_text("LLM_API_KEY=sk-template\nJWT_SECRET=tpl-jwt\n")
        target = tmp_path / ".env"
        backup = env_lib.write_env_file(
            target=target,
            example=example,
            overrides={"LLM_API_KEY": "sk-real", "JWT_SECRET": "real-jwt"},
        )
        assert backup is None  # no prior file
        body = target.read_text()
        assert "LLM_API_KEY=sk-real" in body
        assert "JWT_SECRET=real-jwt" in body

    def test_preserves_existing_user_keys_and_backs_up(self, tmp_path: Path):
        example = tmp_path / ".env.example"
        example.write_text("LLM_API_KEY=sk-template\n")
        target = tmp_path / ".env"
        target.write_text("LLM_API_KEY=sk-old\nMY_CUSTOM=keep-me\n")

        backup = env_lib.write_env_file(
            target=target,
            example=example,
            overrides={"LLM_API_KEY": "sk-new"},
        )
        assert backup is not None
        assert backup.read_text() == "LLM_API_KEY=sk-old\nMY_CUSTOM=keep-me\n"
        body = target.read_text()
        assert "LLM_API_KEY=sk-new" in body
        # User's custom key not in overrides — must be preserved.
        assert "MY_CUSTOM=keep-me" in body

    def test_missing_example_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            env_lib.write_env_file(
                target=tmp_path / ".env",
                example=tmp_path / "missing.example",
                overrides={"FOO": "bar"},
            )
