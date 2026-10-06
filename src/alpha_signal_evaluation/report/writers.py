"""結果の書き出し（parquet / csv / xlsx）。metrics や pipeline に出力処理を漏らさない。"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from ..config import OutputFormat

if TYPE_CHECKING:
    from .report import EvaluationReport

logger = logging.getLogger(__name__)

_EXCEL_MAX_ROWS = 1_048_575
_EXCEL_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")


def _tables(report: "EvaluationReport"):
    for key, run in report.runs.items():
        if run.ok:
            for name, table in run.result.tables.items():
                yield key, name, table


def _has_index(table: pd.DataFrame) -> bool:
    return not isinstance(table.index, pd.RangeIndex)


def _write_files(report: "EvaluationReport", directory: Path, fmt: str) -> None:
    for key, name, table in _tables(report):
        path = directory / key / f"{name}.{fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "parquet":
            table.to_parquet(path, index=_has_index(table))
        else:
            table.to_csv(path, index=_has_index(table))
    summary = report.summary()
    if fmt == "parquet":
        summary.to_parquet(directory / "summary.parquet", index=False)
    else:
        summary.to_csv(directory / "summary.csv", index=False)


def _sheet_name(key: str, name: str, used: set[str]) -> str:
    base = _EXCEL_SHEET_NAME.sub("_", f"{key}.{name}")[:31]
    sheet, i = base, 1
    while sheet in used:
        suffix = f"~{i}"
        sheet, i = base[: 31 - len(suffix)] + suffix, i + 1
    used.add(sheet)
    return sheet


def _write_xlsx(report: "EvaluationReport", directory: Path) -> None:
    path = directory / "report.xlsx"
    used = {"summary", "status"}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        report.summary().to_excel(writer, sheet_name="summary", index=False)
        report.status().to_excel(writer, sheet_name="status", index=False)
        for key, name, table in _tables(report):
            if len(table) > _EXCEL_MAX_ROWS:
                logger.warning("Skip %s.%s in xlsx: too many rows (%d)", key, name, len(table))
                continue
            table.to_excel(writer, sheet_name=_sheet_name(key, name, used), index=_has_index(table))


def _write_run_info(report: "EvaluationReport", directory: Path) -> None:
    notes = {
        key: run.result.notes for key, run in report.runs.items() if run.ok and run.result.notes
    }
    info = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ok": report.ok,
        "status": report.status().to_dict("records"),
        "errors": report.errors,
        "warnings": report.warnings,
        "notes": notes,
        "config": report.config.model_dump(mode="json") if report.config else None,
    }
    with (directory / "run_info.json").open("w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2, default=str)


def write_report(report: "EvaluationReport", directory: Path, formats: list[OutputFormat]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for fmt in dict.fromkeys(formats):
        if fmt == "xlsx":
            _write_xlsx(report, directory)
        else:
            _write_files(report, directory, fmt)
    _write_run_info(report, directory)
    logger.info("Report written to %s", directory)
    return directory
