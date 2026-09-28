"""Схема API (OpenAPI) в stdout — из неё фронтенд генерирует типы: cd frontend && npm run gen:api"""

import json
import sys

from app.main import app

if __name__ == "__main__":
    json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=1)
