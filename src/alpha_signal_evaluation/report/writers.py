"""結果の書き出し（parquet / csv / xlsx）。metrics や pipeline に出力処理を漏らさない。

report 層のうち出力形式だけを扱うモジュール。``EvaluationReport.save()`` から
``write_report()`` が呼ばれる。``directory`` 以下の出力レイアウト:

- ``<key>/<table>.parquet`` / ``<key>/<table>.csv``: 成功した metric の各テーブル
  （key は metric のキー、table は ``MetricResult.tables`` のキー）
- ``summary.parquet`` / ``summary.csv``: 全 metric の summary（long 形式）
- ``report.xlsx``: summary・status シートと、各テーブルを1シートずつ
- ``run_info.json``: 作成日時・成否・状態・エラー・警告・注記・config（常に書く）
- ``by_date/<date>/``: 日付ごとの断面（``by_date`` を指定した場合のみ）。その日付の行だけを
  切り出したテーブルを、上と同じ ``<key>/<table>.<ext>`` / ``report.xlsx`` の形で書く

csv は Excel で開いても日本語が文字化けしないよう UTF-8 の BOM 付き（``utf-8-sig``）で
書く。テーブルの index は RangeIndex でなければ列として書き出す。
"""

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

import pandas as pd

from ..config import ByDate, OutputFormat

if TYPE_CHECKING:
    from .report import EvaluationReport

logger = logging.getLogger(__name__)

_EXCEL_MAX_ROWS = 1_048_575
_CSV_ENCODING = "utf-8-sig"  # BOM 付き。Excel で開いても日本語が文字化けしない
_EXCEL_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")
_BY_DATE_DIR = "by_date"
_UNSAFE_DIR_CHAR = re.compile(r"[^0-9A-Za-z_-]")

# (metric のキー, テーブル名, DataFrame)
TableItem: TypeAlias = tuple[str, str, pd.DataFrame]


def _tables(report: "EvaluationReport"):
    """成功した metric のテーブルを ``(metric のキー, テーブル名, DataFrame)`` で順に返す。"""
    for key, run in report.runs.items():
        if run.ok:
            for name, table in run.result.tables.items():
                yield key, name, table


def _has_index(table: pd.DataFrame) -> bool:
    """テーブルの index を書き出すべきか（RangeIndex 以外なら True）。"""
    return not isinstance(table.index, pd.RangeIndex)


def _write_table(table: pd.DataFrame, path: Path, fmt: str, index: bool) -> None:
    """1つのテーブルを parquet、または csv（UTF-8、BOM 付き）で ``path`` に書く。"""
    if fmt == "parquet":
        table.to_parquet(path, index=index)
    else:
        table.to_csv(path, index=index, encoding=_CSV_ENCODING)


def _write_files(tables: Iterable[TableItem], directory: Path, fmt: str) -> None:
    """各テーブルを ``<directory>/<key>/<table>.<fmt>`` に書く（ディレクトリは作成する）。

    ``fmt`` が ``parquet`` 以外なら csv として UTF-8（BOM 付き）で書く。
    """
    for key, name, table in tables:
        path = directory / key / f"{name}.{fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_table(table, path, fmt, index=_has_index(table))


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


def _write_xlsx(
    tables: Iterable[TableItem],
    directory: Path,
    front: dict[str, pd.DataFrame] | None = None,
    sheets: dict[tuple[str, str], str] | None = None,
) -> None:
    """``<directory>/report.xlsx`` を openpyxl で書く。

    先頭に ``front``（シート名 -> DataFrame。全体の出力では summary・status）、続いて
    各テーブルを1シートずつ書く。シート名は ``sheets``（(key, テーブル名) -> シート名）が
    あればそれを使い、なければ書く順に ``_sheet_name`` で決める。Excel の行数上限を超える
    （1,048,575 行より多い）テーブルは警告をログに出して xlsx からのみ除外する。
    """
    path = directory / "report.xlsx"
    front = front or {}
    used = set(front)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, frame in front.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
        for key, name, table in tables:
            if len(table) > _EXCEL_MAX_ROWS:
                logger.warning("Skip %s.%s in xlsx: too many rows (%d)", key, name, len(table))
                continue
            sheet = sheets[key, name] if sheets else _sheet_name(key, name, used)
            table.to_excel(writer, sheet_name=sheet, index=_has_index(table))


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


def _write_by_date(
    report: "EvaluationReport", directory: Path, formats: list[OutputFormat], by_date: ByDate
) -> None:
    """日付ごとの断面を ``<directory>/by_date/<date>/`` に書く。

    ``report.by_date()`` で切り出した各日付のテーブルを、全体の出力と同じ形
    （``<key>/<table>.<ext>``、xlsx は ``report.xlsx`` に1テーブル1シート）で書く。
    summary・status は期間全体の値なので日付ごとには書かない。

    書くものがない場合は警告をログに出す: 断面が1つもない（日付を持つテーブルがない）
    場合と、``by_date`` のリストにあってどのテーブルにも行がない日付。xlsx のシート名は
    全テーブルから1回だけ決め、日付によってテーブルの有無が違っても同じテーブルが
    同じシート名になるようにする。ディレクトリ名は日付キーの英数字・``_``・``-`` 以外を
    ``_`` に置き換えたもの（日付キーが規約どおり数字だけならそのまま）。
    """
    if not formats:
        return
    sections = report.by_date(None if by_date == "all" else by_date)
    if not sections:
        logger.warning("output.by_date is set but no table has rows for it; nothing is written")
        return
    if by_date != "all" and (missing := sorted({str(d) for d in by_date} - set(sections))):
        logger.warning("No table has rows for by_date dates %s", missing)
    used = {"summary", "status"}  # 全体の report.xlsx と同じ名前から始める
    sheets = {(key, name): _sheet_name(key, name, used) for key, name, _ in _tables(report)}
    for date, metrics in sections.items():
        dirname = _UNSAFE_DIR_CHAR.sub("_", date)
        if not dirname:
            logger.warning("Skip by_date for an empty date key")
            continue
        target = directory / _BY_DATE_DIR / dirname
        target.mkdir(parents=True, exist_ok=True)
        items = [(key, name, t) for key, tables in metrics.items() for name, t in tables.items()]
        for fmt in formats:
            if fmt == "xlsx":
                _write_xlsx(items, target, sheets=sheets)
            else:
                _write_files(items, target, fmt)


def write_report(
    report: "EvaluationReport",
    directory: Path,
    formats: list[OutputFormat],
    by_date: ByDate | None = None,
) -> Path:
    """EvaluationReport を指定の形式で ``directory`` に書き出す。

    ディレクトリは親も含めて作成する。``formats`` の重複は1回だけ扱い（順序は保つ）、
    ``xlsx`` は report.xlsx を、それ以外（``parquet`` / ``csv``）はテーブルごとの
    ファイルと summary を書く。``by_date`` を指定すると、日付ごとの断面も同じ形式で
    ``by_date/<date>/`` に書く。run_info.json は形式によらず書く（断面より先に書くので、
    断面の書き出しに失敗しても実行の記録は残る）。
    既存のファイルは上書きする（以前の実行で書いたファイルは消さない）。

    Args:
        report: 書き出す評価結果。
        directory: 出力先ディレクトリ。
        formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）のリスト。
        by_date: 日付ごとの断面を出す対象。``"all"`` なら全日付、日付キーのリストなら
            その日付だけ。``None`` か空リストなら出さない。

    Returns:
        ``directory``（そのまま）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    formats = list(dict.fromkeys(formats))
    for fmt in formats:
        if fmt == "xlsx":
            front = {"summary": report.summary(), "status": report.status()}
            _write_xlsx(_tables(report), directory, front)
        else:
            _write_files(_tables(report), directory, fmt)
            _write_table(report.summary(), directory / f"summary.{fmt}", fmt, index=False)
    _write_run_info(report, directory)
    if by_date:
        _write_by_date(report, directory, formats, by_date)
    logger.info("Report written to %s", directory)
    return directory
