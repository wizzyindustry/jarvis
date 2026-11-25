"""Tokens live in Windows Credential Manager (via keyring), never in files."""
from __future__ import annotations

import json
from typing import Any

import keyring

SERVICE = "jarvis-assistant"


def get(name: str) -> dict[str, Any] | None:
    raw = keyring.get_password(SERVICE, name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def put(name: str, value: dict[str, Any]) -> None:
    keyring.set_password(SERVICE, name, json.dumps(value))


def delete(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass
