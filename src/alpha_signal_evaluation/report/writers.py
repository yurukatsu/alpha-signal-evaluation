"""結果の書き出し（parquet / csv / xlsx）。metrics や pipeline に出力処理を漏らさない。

report 層のうち出力形式だけを扱うモジュール。``EvaluationReport.save()`` から
``write_report()`` が呼ばれる。``directory`` 以下の出力レイアウト:

- ``<key>/<table>.parquet`` / ``<key>/<table>.csv``: 成功した metric の各テーブル
  （key は metric のキー、table は ``MetricResult.tables`` のキー）
- ``summary.parquet`` / ``summary.csv``: 全 metric の summary（long 形式）
- ``report.xlsx``: summary・status シートと、各テーブルを1シートずつ
- ``run_info.json``: 作成日時・成否・状態・エラー・警告・注記・config（常に書く）

csv は Excel で開いても日本語が文字化けしないよう UTF-8 の BOM 付き（``utf-8-sig``）で
書く。テーブルの index は RangeIndex でなければ列として書き出す。
"""

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
_CSV_ENCODING = "utf-8-sig"  # BOM 付き。Excel で開いても日本語が文字化けしない
_EXCEL_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")


def _tables(report: "EvaluationReport"):
    """成功した metric のテーブルを ``(metric のキー, テーブル名, DataFrame)`` で順に返す。"""
    for key, run in report.runs.items():
        if run.ok:
            for name, table in run.result.tables.items():
                yield key, name, table


def _has_index(table: pd.DataFrame) -> bool:
    """テーブルの index を書き出すべきか（RangeIndex 以外なら True）。"""
    return not isinstance(table.index, pd.RangeIndex)


def _write_files(report: "EvaluationReport", directory: Path, fmt: str) -> None:
    """各テーブルと summary を parquet または csv のファイルに書く。

    テーブルは ``<directory>/<key>/<table>.<fmt>``（ディレクトリは作成する）、summary は
    ``<directory>/summary.<fmt>``（index なし）。``fmt`` が ``parquet`` 以外なら csv
    として UTF-8（BOM 付き）で書く。
    """
    for key, name, table in _tables(report):
        path = directory / key / f"{name}.{fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "parquet":
            table.to_parquet(path, index=_has_index(table))
        else:
            table.to_csv(path, index=_has_index(table), encoding=_CSV_ENCODING)
    summary = report.summary()
    if fmt == "parquet":
        summary.to_parquet(directory / "summary.parquet", index=False)
    else:
        summary.to_csv(directory / "summary.csv", index=False, encoding=_CSV_ENCODING)


def _sheet_name(key: str, name: str, used: set[str]) -> str:
    """Excel のシート名を ``<key>.<name>`` から作り、``used`` に追加して返す。

    Excel で使えない文字（角括弧・コロン・アスタリスク・疑問符・スラッシュ・
    バックスラッシュ）を ``_`` に置き換えて31文字に切り詰める。``used`` と重複する
    場合は末尾を ``~1``, ``~2``, ... に置き換えて（31文字以内のまま）一意にする。
    """
    base = _EXCEL_SHEET_NAME.sub("_", f"{key}.{name}")[:31]
    sheet, i = base, 1
    while sheet in used:
        suffix = f"~{i}"
        sheet, i = base[: 31 - len(suffix)] + suffix, i + 1
    used.add(sheet)
    return sheet


def _write_xlsx(report: "EvaluationReport", directory: Path) -> None:
    """``<directory>/report.xlsx`` を openpyxl で書く。

    先頭に summary・status シート、続いて成功した metric の各テーブルを1シートずつ
    書く（シート名は ``_sheet_name``）。Excel の行数上限を超える（1,048,575 行より多い）
    テーブルは警告をログに出して xlsx からのみ除外する。
    """
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
    """実行情報を ``<directory>/run_info.json``（UTF-8、インデント2）に書く。

    キーは created_at（ローカル時刻の ISO 形式、秒まで）、ok、status（``status()`` の
    レコード）、errors（キー -> トレースバック）、warnings、notes（注記のある成功
    metric のみ）、config（JSON 化した config、なければ null）。JSON にできない値は
    ``str`` で文字列にする。
    """
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
    """EvaluationReport を指定の形式で ``directory`` に書き出す。

    ディレクトリは親も含めて作成する。``formats`` の重複は1回だけ扱い（順序は保つ）、
    ``xlsx`` は report.xlsx を、それ以外（``parquet`` / ``csv``）はテーブルごとの
    ファイルと summary を書く。最後に形式によらず run_info.json を書く。
    既存のファイルは上書きする。

    Args:
        report: 書き出す評価結果。
        directory: 出力先ディレクトリ。
        formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）のリスト。

    Returns:
        ``directory``（そのまま）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    for fmt in dict.fromkeys(formats):
        if fmt == "xlsx":
            _write_xlsx(report, directory)
        else:
            _write_files(report, directory, fmt)
    _write_run_info(report, directory)
    logger.info("Report written to %s", directory)
    return directory
