"""結果の書き出し（parquet / csv / xlsx）。metrics や pipeline に出力処理を漏らさない。

report 層のうち出力形式だけを扱うモジュール。``EvaluationReport.save()`` から
``write_report()`` が呼ばれる。``directory`` 以下の出力レイアウト:

- ``<key>/<table>.parquet`` / ``<key>/<table>.csv``: 成功した metric の各テーブル
  （key は metric のキー、table は ``MetricResult.tables`` のキー）
- ``summary.parquet`` / ``summary.csv``: 全 metric の summary（long 形式）
- ``report.xlsx``: summary・status シートと、各テーブルを1シートずつ
- ``run_info.json``: 作成日時・実行の状態（``state`` / ``pending``）・成否・metric ごとの
  状態・エラー・警告・注記・config（常に書く）
- ``by_date/<date>/``: 日付ごとの断面（``by_date`` を指定した場合のみ）。その日付の行だけを
  切り出したテーブルを、上と同じ ``<key>/<table>.<ext>`` / ``report.xlsx`` の形で書く

csv は Excel で開いても日本語が文字化けしないよう UTF-8 の BOM 付き（``utf-8-sig``）で
書く。テーブルの index は RangeIndex でなければ列として書き出す。

どのファイルも同じディレクトリの一時ファイル（``.tmp-<名前>``）に書いてから置き換える。
書いている途中でプロセスが落ちても、既にあるファイルが壊れた状態で残らない
（強制終了の場合は一時ファイルが残ることがある）。
"""

import json
import logging
import os
import re
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

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
# 実行の状態。running = 実行中・書き出し中（このまま残っていれば強制終了された）、
# finished = 全 metric を実行し、すべて書き終えた、aborted = 例外や Ctrl-C で中断した
RunState: TypeAlias = Literal["running", "finished", "aborted"]


@contextmanager
def _atomic(path: Path) -> Iterator[Path]:
    """``path`` と同じディレクトリの一時ファイルのパスを渡し、正常に抜けたら ``path`` に置き換える。

    一時ファイルは ``.tmp-<名前>``（拡張子は元のまま）。例外で抜けた場合は一時ファイルを
    消し、``path`` は元のまま残す。
    """
    tmp = path.with_name(f".tmp-{path.name}")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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
    with _atomic(path) as tmp:
        if fmt == "parquet":
            table.to_parquet(tmp, index=index)
        else:
            table.to_csv(tmp, index=index, encoding=_CSV_ENCODING)


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
    with _atomic(path) as tmp, pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        for sheet, frame in front.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
        for key, name, table in tables:
            if len(table) > _EXCEL_MAX_ROWS:
                logger.warning("Skip %s.%s in xlsx: too many rows (%d)", key, name, len(table))
                continue
            sheet = sheets[key, name] if sheets else _sheet_name(key, name, used)
            table.to_excel(writer, sheet_name=sheet, index=_has_index(table))


def write_run_info(
    report: "EvaluationReport",
    directory: Path,
    state: RunState = "finished",
    pending: Iterable[str] = (),
) -> None:
    """実行情報を ``<directory>/run_info.json``（UTF-8、インデント2）に書く。

    キーは created_at（ローカル時刻の ISO 形式、秒まで）、state（実行の状態）、pending
    （まだ結果のない metric のキー）、ok、status（``status()`` のレコード）、errors
    （キー -> トレースバック）、warnings、notes（注記のある成功 metric のみ）、config
    （JSON 化した config、なければ null）。JSON にできない値は ``str`` で文字列にする。

    ``state`` は ``finished``（全 metric を実行し、すべて書き終えた）、``running``（実行中、
    または書き出し中。このまま残っていればプロセスが強制終了されている）、``aborted``
    （例外や Ctrl-C で中断した）。``ok`` は ``finished`` で全 metric が成功したときだけ true。
    ``report`` に含まれるのは結果の出た metric だけなので、status・errors・notes に
    ``pending`` の metric は現れない。

    Args:
        report: 書き出す評価結果（途中経過なら、その時点までに終わった metric だけを持つ）。
        directory: 出力先ディレクトリ（作成済みであること）。
        state: 実行の状態。
        pending: まだ結果のない metric のキー。
    """
    notes = {
        key: run.result.notes for key, run in report.runs.items() if run.ok and run.result.notes
    }
    info = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "state": state,
        "pending": list(pending),
        "ok": report.ok and state == "finished",
        "status": report.status().to_dict("records"),
        "errors": report.errors,
        "warnings": report.warnings,
        "notes": notes,
        "config": report.config.model_dump(mode="json") if report.config else None,
    }
    with _atomic(directory / "run_info.json") as tmp, tmp.open("w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2, default=str)


def write_by_date(
    report: "EvaluationReport",
    directory: Path,
    formats: list[OutputFormat],
    by_date: ByDate,
    warn: bool = True,
    skip: Collection[str] = (),
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

    ``warn=False`` なら上の警告を出さない（逐次書き出しで metric を1つずつ渡すときは、
    日付を持つテーブルのない metric があるのが普通なので、最後の全体の書き出しでだけ警告する）。
    ``skip`` の metric は parquet / csv を書かない（逐次書き出しで既に書いたものを書き直さない
    ため）。xlsx は1つのブックに全テーブルを入れるので、``skip`` によらずすべて書く。
    """
    if not formats:
        return
    sections = report.by_date(None if by_date == "all" else by_date)
    if not sections:
        if warn:
            logger.warning("output.by_date is set but no table has rows for it; nothing is written")
        return
    missing = [] if by_date == "all" else sorted({str(d) for d in by_date} - set(sections))
    if warn and missing:
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
        fresh = [item for item in items if item[0] not in skip]
        for fmt in formats:
            if fmt == "xlsx":
                _write_xlsx(items, target, sheets=sheets)
            else:
                _write_files(fresh, target, fmt)


_TABLE_SUFFIXES = frozenset({".csv", ".parquet"})


def clear_metric_files(directory: Path, key: str, suffixes: Collection[str]) -> None:
    """以前の実行で書いた、ある metric の表のファイルを消す（書き直す前に呼ぶ）。

    対象は ``<directory>/<key>/`` と ``<directory>/by_date/<日付>/<key>/`` の中の、拡張子が
    ``suffixes`` のファイル（書きかけの一時ファイル ``.tmp-*`` を含む）だけ。それ以外の
    ファイルとディレクトリには触れない。空になったディレクトリは消す。

    今回の実行で書かない表（失敗した metric の表、今回は行のない日付の断面、metric の
    変更でなくなった表）が以前の実行のまま残り、今回の結果と取り違えられるのを防ぐ。
    metric のキーは config（``id``）と登録（``name``）で文字種を制限しているが、念のため
    出力先の直下を指さないキーは何もしない。シンボリックリンクのディレクトリとファイルは
    たどらず、消さない（出力先の外のファイルを消さないため）。

    Args:
        directory: 出力先ディレクトリ。
        key: metric のキー。
        suffixes: 消すファイルの拡張子（例: ``{".csv"}``）。
    """
    if Path(key).name != key or key in (".", ".."):
        logger.warning("Skip clearing files for an unsafe metric key %r", key)
        return
    targets = [directory / key]
    by_date = directory / _BY_DATE_DIR
    if by_date.is_dir() and not by_date.is_symlink():
        days = [d for d in sorted(by_date.iterdir()) if d.is_dir() and not d.is_symlink()]
        targets += [day / key for day in days]
    for target in targets:
        if target.is_symlink() or not target.is_dir():
            continue
        for path in target.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes:
                path.unlink()
        for empty in (target, target.parent):
            if empty in (directory, by_date) or empty.is_symlink() or not empty.is_dir():
                continue
            if not any(empty.iterdir()):
                empty.rmdir()


def table_suffixes(formats: Iterable[OutputFormat], ok: bool) -> frozenset[str]:
    """``clear_metric_files`` で消す拡張子。

    成功した metric は今回書く形式（csv / parquet）のファイルだけを置き換え、ほかの形式の
    ファイルは残す。失敗した metric は今回の表がないので、csv / parquet をすべて消す。
    """
    if not ok:
        return _TABLE_SUFFIXES
    return frozenset(f".{fmt}" for fmt in formats if fmt != "xlsx")


def write_tables(
    report: "EvaluationReport", directory: Path, fmt: OutputFormat, skip: Collection[str] = ()
) -> None:
    """成功した metric の各テーブルを parquet または csv で ``<key>/<table>.<fmt>`` に書く。

    ``skip`` の metric は書かない（逐次書き出しで既に書いたものを書き直さないため）。
    """
    _write_files((item for item in _tables(report) if item[0] not in skip), directory, fmt)


def write_summary(report: "EvaluationReport", directory: Path, fmt: OutputFormat) -> None:
    """全 metric の summary（long 形式）を ``summary.<fmt>``（parquet または csv）に書く。"""
    _write_table(report.summary(), directory / f"summary.{fmt}", fmt, index=False)


def write_report(
    report: "EvaluationReport",
    directory: Path,
    formats: list[OutputFormat],
    by_date: ByDate | None = None,
    state: RunState = "finished",
    pending: Iterable[str] = (),
    skip: Collection[str] = (),
) -> Path:
    """EvaluationReport を指定の形式で ``directory`` に書き出す。

    ディレクトリは親も含めて作成する。``formats`` の重複は1回だけ扱い（順序は保つ）、
    ``xlsx`` は report.xlsx を、それ以外（``parquet`` / ``csv``）はテーブルごとの
    ファイルと summary を書く。``by_date`` を指定すると、日付ごとの断面も同じ形式で
    ``by_date/<date>/`` に書く。run_info.json は形式によらず、すべて書き終えた最後に書く
    （``state: finished`` が残っていれば、断面まで含めて書けている）。断面を書く場合は、
    その前にも ``state: running`` で書いておく（断面の書き出しに失敗しても実行の記録は残る）。
    既存のファイルは上書きする。``report`` の metric（``skip`` を除く）については、書く前に
    以前の実行の表（成功した metric は今回書く形式、失敗した metric は csv / parquet の
    すべて）を消す（``clear_metric_files``）。``report`` にない metric のファイルには触れない。

    Args:
        report: 書き出す評価結果。
        directory: 出力先ディレクトリ。
        formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）のリスト。
        by_date: 日付ごとの断面を出す対象。``"all"`` なら全日付、日付キーのリストなら
            その日付だけ。``None`` か空リストなら出さない。
        state: run_info.json に記録する実行の状態（``write_run_info`` を参照）。中断した
            実行の途中までの結果を書くときは ``"aborted"``。
        pending: run_info.json に記録する、まだ結果のない metric のキー。
        skip: parquet / csv のテーブルと断面を書かない metric のキー（逐次書き出しで既に
            書いたもの）。summary・xlsx・run_info.json は ``skip`` によらず書く。

    Returns:
        ``directory``（そのまま）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    formats = list(dict.fromkeys(formats))
    # 以前の実行の表を、今回書く metric の分だけ消してから書く（失敗した metric は全部）
    for key, run in report.runs.items():
        if key not in skip:
            clear_metric_files(directory, key, table_suffixes(formats, run.ok))
    for fmt in formats:
        if fmt == "xlsx":
            front = {"summary": report.summary(), "status": report.status()}
            _write_xlsx(_tables(report), directory, front)
        else:
            write_tables(report, directory, fmt, skip)
            write_summary(report, directory, fmt)
    if by_date:
        # 断面を書いている間に落ちても、完了した実行に見えないようにする
        write_run_info(report, directory, "running" if state == "finished" else state, pending)
        write_by_date(report, directory, formats, by_date, skip=skip)
    write_run_info(report, directory, state, pending)
    logger.info("Report written to %s", directory)
    return directory
