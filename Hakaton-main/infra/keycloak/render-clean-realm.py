#!/usr/bin/env python3
"""Render the ignored clean Keycloak realm import from local credentials."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "realm-stroykontrol.clean.template.json"
DEFAULT_ENV = HERE / "clean.env"
DEFAULT_OUTPUT = HERE / "generated" / "realm.json"
REQUIRED = {"KC_DB_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD", "KC_SERVICE_CLIENT_SECRET", "KC_SITE_ADMIN_PASSWORD"}
ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
PLACEHOLDERS = {
    "KC_SERVICE_CLIENT_SECRET": "__KC_SERVICE_CLIENT_SECRET__",
    "KC_SITE_ADMIN_PASSWORD": "__KC_SITE_ADMIN_PASSWORD__",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not ENV_KEY.fullmatch(key):
            raise ValueError(f"{path}:{number}: expected NAME=value")
        if key in values:
            raise ValueError(f"{path}:{number}: duplicate variable {key}")
        values[key] = value
    missing = sorted(name for name in REQUIRED if not values.get(name))
    if missing:
        raise ValueError(f"{path}: missing non-empty " + ", ".join(missing))
    return values


def render(template: object, values: dict[str, str]) -> object:
    if isinstance(template, dict):
        return {key: render(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [render(value, values) for value in template]
    if isinstance(template, str):
        for variable, placeholder in PLACEHOLDERS.items():
            if template == placeholder:
                return values[variable]
    return template


def main() -> None:
    if len(sys.argv) > 3:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} [clean.env] [output.json]")
    env_path = Path(sys.argv[1]) if len(sys.argv) >= 2 else DEFAULT_ENV
    output_path = Path(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_OUTPUT
    values = read_env(env_path)
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    rendered = render(template, values)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.touch(mode=0o600, exist_ok=True)
    output_path.chmod(0o600)
    output_path.write_text(json.dumps(rendered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
