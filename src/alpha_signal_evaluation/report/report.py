"""評価結果 ``EvaluationReport``。結果の参照・集約・保存の入口。

``api.evaluate()`` が ``pipeline.runner.run_metrics()`` の結果（metric のキー ->
MetricRun）と警告から作る。失敗した metric があってもレポートは作られ、成否は
``ok`` / ``errors`` / ``status()`` で確認できる。ファイルへの書き出しの実装は
``writers.py`` にあり、ここでは出力先と形式の決定だけを行う。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

import pandas as pd

from ..config import ByDate, EvaluationConfig, OutputFormat
from ..data.columns import DATE
from ..pipeline.runner import MetricRun

# 日付 -> metric のキー -> テーブル名 -> その日付の行
DateTables: TypeAlias = dict[str, dict[str, dict[str, pd.DataFrame]]]


@dataclass
class EvaluationReport:
    """評価結果。metric のキー（config の id または name）ごとに MetricRun を持つ。

    成功した metric の結果は ``report[key]`` で MetricResult として取り出す。

    Attributes:
        runs: metric のキー -> MetricRun（config の順序）。
        config: 評価に使った config。``save()`` で出力先・形式を省略したときの既定値と、
            run_info.json への記録に使う。``api.evaluate()`` からは常に設定される。
        warnings: データの検証・準備中の警告文。run_info.json に記録される。
    """

    runs: dict[str, MetricRun]
    config: EvaluationConfig | None = None
    warnings: list[str] = field(default_factory=list)

    def __getitem__(self, key: str):
        """成功した metric の MetricResult を返す。

        Args:
            key: metric のキー（config の ``id``、なければ ``name``）。

        Returns:
            その metric の MetricResult（``tables`` / ``summary`` / ``notes``）。

        Raises:
            KeyError: ``key`` の metric がない場合。
            RuntimeError: その metric が失敗していた場合（メッセージにトレースバックを含む）。
        """
        run = self.runs[key]
        if not run.ok:
            raise RuntimeError(f"Metric {key!r} failed:\n{run.error}")
        return run.result

    @property
    def ok(self) -> bool:
        """全 metric が成功したか（metric が1つもなければ True）。"""
        return all(run.ok for run in self.runs.values())

    @property
    def errors(self) -> dict[str, str]:
        """失敗した metric のキー -> トレースバック文字列。全て成功なら空 dict。"""
        return {key: run.error for key, run in self.runs.items() if not run.ok}

    def summary(self) -> pd.DataFrame:
        """全 metric の summary を long 形式（metric, stat, value）にまとめる。

        成功した metric の ``MetricResult.summary``（統計量名 -> 値）を1行1統計量に
        展開する。失敗した metric と summary が空の metric は含まれない。

        Returns:
            columns=[metric, stat, value]（RangeIndex）の DataFrame。metric は metric の
            キー。該当する行がなければ列だけを持つ空の DataFrame。
        """
        rows = [
            {"metric": key, "stat": stat, "value": value}
            for key, run in self.runs.items()
            if run.ok
            for stat, value in run.result.summary.items()
        ]
        return pd.DataFrame(rows, columns=["metric", "stat", "value"])

    def status(self) -> pd.DataFrame:
        """metric ごとの実行状況の一覧。

        Returns:
            1行1 metric（config の順序）、columns=[metric, name, status, elapsed_sec,
            error] の DataFrame。status は ``"ok"`` / ``"failed"``、elapsed_sec は実行時間
            （秒、小数第3位に丸める）、error はトレースバックの最終行（例外の型と
            メッセージ）で成功時は空文字。metric が1つもなければ列もない空の DataFrame。
        """
        return pd.DataFrame(
            [
                {
                    "metric": key,
                    "name": run.name,
                    "status": "ok" if run.ok else "failed",
                    "elapsed_sec": round(run.elapsed, 3),
                    "error": _last_line(run.error),
                }
                for key, run in self.runs.items()
            ]
        )

    def by_date(self, dates: Iterable[str] | None = None) -> DateTables:
        """日付を持つテーブルを日付ごとに切り出す（ある時点の断面）。

        成功した metric のテーブルのうち ``date`` 列（または index の ``date`` レベル）を
        持つものが対象で、metric 側の対応は要らない。各テーブルはその日付の行だけを残し、
        ``date`` 列を除く（他の列・行の順序は元のまま）。日付を持たないテーブル（期間全体の
        summary 等）と、その日付に行がないテーブルは含まれない。値は加工しないので、
        欠損の行は欠損のまま残る（日付が欠損の行はどの断面にも入らない）。

        日付は文字列に直して照合する（日付キーは文字列が規約だが、int 等の日付を持つ
        DataBundle を直接渡した場合でも ``dates()`` の値でそのまま引けるようにするため）。
        ``date`` が列と index の両方にあるテーブルは列を使う。

        Args:
            dates: 取り出す日付キー。省略時はテーブルに現れる全日付。どのテーブルにも
                ない日付は結果に含まれない。

        Returns:
            日付（文字列、昇順） -> metric のキー -> テーブル名 -> DataFrame。
        """
        wanted = None if dates is None else {str(d) for d in dates}
        out: DateTables = {}
        for key, run in self.runs.items():
            if not run.ok:
                continue
            for name, table in run.result.tables.items():
                if DATE not in table.columns:
                    if DATE not in table.index.names:
                        continue
                    table = table.reset_index(DATE)
                ranged = isinstance(table.index, pd.RangeIndex)
                # 欠損の日付は "nan" という断面にせず、groupby で落とす
                keys = table[DATE].astype(str).where(table[DATE].notna())
                for date, rows in table.groupby(keys, sort=False):
                    if wanted is not None and date not in wanted:
                        continue
                    rows = rows.drop(columns=DATE)
                    if ranged:
                        rows = rows.reset_index(drop=True)
                    out.setdefault(date, {}).setdefault(key, {})[name] = rows
        return dict(sorted(out.items()))

    def dates(self) -> list[str]:
        """``by_date()`` / ``at()`` で取り出せる日付キー（昇順）。"""
        return list(self.by_date())

    def at(self, date: str) -> dict[str, dict[str, pd.DataFrame]]:
        """1つの日付の断面を返す（リバランス時点ごとに全シグナルを見比べる用途）。

        Args:
            date: 日付キー（``yyyymmdd`` / ``yyyymm``）。

        Returns:
            metric のキー -> テーブル名 -> その日付の行（``date`` 列なし）。

        Raises:
            KeyError: その日付の行を持つテーブルがない場合。

        Examples:
            >>> report.at("20240531")["ic"]["timeseries"]  # doctest: +SKIP
        """
        date = str(date)
        tables = self.by_date([date])
        if date not in tables:
            raise KeyError(f"No table has rows for date {date!r}")
        return tables[date]

    def save(
        self,
        directory: str | Path | None = None,
        formats: list[OutputFormat] | None = None,
        by_date: ByDate | None = None,
    ) -> Path:
        """結果を書き出す。省略時は config の output 設定を使う。

        ``directory`` と ``formats`` のうち省略した方だけを config の ``output.dir`` /
        ``output.formats`` で補う。``by_date`` も省略時は config の ``output.by_date``
        （config がなければ出さない）。書き出す内容（形式の重複は1回だけ扱う）:

        - parquet / csv: 成功した metric の各テーブルを ``<directory>/<key>/<table>.<ext>``、
          全 metric の summary を ``<directory>/summary.<ext>`` に書く。
          csv は UTF-8（BOM 付き）。
        - xlsx: ``<directory>/report.xlsx``（summary・status シートと各テーブルのシート）。
        - 形式によらず ``<directory>/run_info.json``（状態・エラー・警告・注記・config）。
        - 書く前に、``runs`` の metric の以前の表（成功した metric は今回書く形式、失敗した
          metric は csv / parquet のすべて）を消す。ほかの metric のファイルには触れない。
        - ``by_date`` を指定した場合: 日付ごとの断面（``by_date()``）を同じ形式で
          ``<directory>/by_date/<date>/<key>/<table>.<ext>`` と
          ``<directory>/by_date/<date>/report.xlsx`` に書く。

        Args:
            directory: 出力先ディレクトリ（なければ親も含めて作成する）。
            formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）のリスト。
            by_date: 日付ごとの断面を出す対象。``"all"`` なら全日付、日付キーのリストなら
                その日付だけ（空リストなら出さない）。

        Returns:
            出力先ディレクトリの Path。

        Raises:
            ValueError: ``directory`` か ``formats`` を省略し、かつ ``config`` がない場合。
        """
        from .writers import write_report

        if directory is None or formats is None:
            if self.config is None:
                raise ValueError("directory and formats are required when config is not set")
            directory = directory or self.config.output.dir
            formats = formats or self.config.output.formats
        if by_date is None and self.config is not None:
            by_date = self.config.output.by_date
        return write_report(self, Path(directory), formats, by_date)


def _last_line(text: str | None) -> str:
    """テキスト（トレースバック）の前後の空白を除いた最終行を返す。None や空なら空文字。"""
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""
