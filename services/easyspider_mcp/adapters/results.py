from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from packages.crawl_core.errors import AdapterError


class ResultReader:
    def __init__(self, data_root: str | Path, max_output_bytes: int = 100_000_000):
        self.data_root = Path(data_root).resolve()
        self.max_output_bytes = max_output_bytes

    def read(self, execution_id: int, limit: int = 20) -> dict[str, Any]:
        if execution_id < 0:
            raise AdapterError("execution_id must be non-negative")
        folder = (self.data_root / f"Task_{execution_id}").resolve()
        if folder.parent != self.data_root:
            raise AdapterError("invalid execution output path")
        if not folder.exists():
            return {"records": [], "record_count": 0, "output_files": [], "status": "not_ready"}
        candidates = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {".json", ".csv", ".xlsx"}),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        output_files = [str(path) for path in candidates]
        if not candidates:
            return {"records": [], "record_count": 0, "output_files": [], "status": "no_result"}
        path = candidates[0]
        if path.stat().st_size > self.max_output_bytes:
            raise AdapterError(f"output exceeds {self.max_output_bytes} bytes")
        records = self._parse(path)
        return {
            "records": records[:limit],
            "record_count": len(records),
            "output_files": output_files,
            "status": "ready",
        }

    def _parse(self, path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".json":
            text = path.read_text(encoding="utf-8-sig")
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = [json.loads(line) for line in text.splitlines() if line.strip()]
            if isinstance(value, dict):
                value = value.get("records", [value])
            if not isinstance(value, list):
                raise AdapterError(f"unsupported JSON result shape in {path.name}")
            return [item if isinstance(item, dict) else {"value": item} for item in value]
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise AdapterError("install the 'xlsx' extra to read XLSX results") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value) if value is not None else "" for value in next(rows, [])]
        return [dict(zip(headers, row, strict=False)) for row in rows]

