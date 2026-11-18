"""逐次書き出し。metric が1つ終わるたびに、その時点までの結果をファイルに残す。

``EvaluationReport.save()`` は全 metric が終わってから書き出すので、途中でプロセスが落ちる
（強制終了・メモリ不足・Ctrl-C・データ読み込み中の例外）と何も残らない。
``IncrementalWriter`` は ``api.evaluate(save=...)`` から使われ、``pipeline.runner`` の
``on_result`` で完了した metric を受け取って順に書き出す。

- ``start()``: 計算を始める前に run_info.json を ``state: running``・全 metric が ``pending``
  で書く（以前の実行の run_info.json が残って、終わった実行に見えるのを防ぐ）。
- ``add()``: 完了した metric の各テーブルと日付ごとの断面（parquet / csv）を書き、
  summary と run_info.json をその時点の内容に更新する。
- ``finish()``: 全 metric が終わった後、残り（xlsx と、まだ書けていない metric）を書いて
  run_info.json を ``state: finished`` にする。最終的な出力は ``save()`` と同じになる。
- ``abort()``: 例外や Ctrl-C で中断したとき、そこまでに終わった metric の分を
  ``save()`` と同じ形（xlsx を含む）で書き、run_info.json を ``state: aborted`` にする。

xlsx は1つのブックに全テーブルを入れるため、``finish()`` / ``abort()`` でだけ書く
（metric が終わるたびに全体を書き直すと、テーブルが大きいほど時間がかかるため）。
プロセスが強制終了された場合は ``abort()`` が呼ばれないので、残るのは最後の ``add()`` までの
parquet / csv と、``state: running`` のままの run_info.json になる。

実行中の run_info.json と summary に載せるのは「ファイルに書けた metric」だけにする。
計算は終わったが書き出しに失敗した metric は ``pending`` に残し、次の ``add()`` と
``finish()`` / ``abort()`` で書き直す（run_info.json が、ディスクにない結果を「ある」と
言わないようにするため）。

出力先に以前の実行のファイルがある場合、metric を書くときにその metric の以前の表を消して
から書く（失敗した metric のディレクトリに以前の結果が残らないようにするため）。まだ
終わっていない metric（``pending``）と、今回の config にない metric のファイルには触れないので、
``pending`` の metric のディレクトリにファイルがあれば、それは以前の実行のものである。
"""

import logging
from collections.abc import Iterable
from pathlib import Path

from ..config import ByDate, EvaluationConfig, OutputFormat
from ..pipeline.runner import MetricRun
from .report import EvaluationReport
from .writers import (
    clear_metric_files,
    table_suffixes,
    write_by_date,
    write_report,
    write_run_info,
    write_summary,
    write_tables,
)

logger = logging.getLogger(__name__)


class IncrementalWriter:
    """metric が完了するたびに結果を書き出す。

    Attributes:
        directory: 出力先ディレクトリ。
        formats: 出力形式（重複を除いたもの）。
        by_date: 日付ごとの断面を出す対象（``None`` か空リストなら出さない）。
        keys: 実行する全 metric のキー（config の順序）。
        config: 評価に使う config（run_info.json に記録する）。
        warnings: run_info.json に記録する警告文。データの準備が終わってから増えるので、
            呼び出し側が持つリストをそのまま参照する。
    """

    def __init__(
        self,
        directory: str | Path,
        formats: Iterable[OutputFormat],
        by_date: ByDate | None,
        keys: Iterable[str],
        config: EvaluationConfig | None = None,
        warnings: list[str] | None = None,
    ):
        """書き出し先と、実行する metric のキーを受け取る（この時点では何も書かない）。

        Args:
            directory: 出力先ディレクトリ（なければ ``start()`` で親も含めて作成する）。
            formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）。
            by_date: 日付ごとの断面を出す対象。
            keys: 実行する全 metric のキー（config の順序）。
            config: 評価に使う config。
            warnings: 警告文のリスト（コピーせずに参照する）。
        """
        self.directory = Path(directory)
        self.formats: list[OutputFormat] = list(dict.fromkeys(formats))
        self.by_date = by_date
        self.keys = list(keys)
        self.config = config
        self.warnings = warnings if warnings is not None else []
        self._done: dict[str, MetricRun] = {}  # 計算が終わった metric（成功・失敗とも）
        self._written: set[str] = set()  # そのうち、結果をファイルに書けた metric

    @property
    def pending(self) -> list[str]:
        """まだ結果をファイルに書けていない metric のキー（config の順序）。"""
        return [key for key in self.keys if key not in self._written]

    def _report(self, written_only: bool = True) -> EvaluationReport:
        """ここまでの metric だけを持つレポート（完了順ではなく config の順序）。

        ``written_only=True`` ならファイルに書けた metric だけ、``False`` なら計算が
        終わった metric すべて。
        """
        runs = {
            key: self._done[key]
            for key in self.keys
            if key in self._done and (not written_only or key in self._written)
        }
        return EvaluationReport(runs=runs, config=self.config, warnings=self.warnings)

    def start(self) -> None:
        """出力先を作り、run_info.json を ``state: running``・全 metric が pending で書く。

        Raises:
            OSError: 出力先に書けない場合（計算を始める前に分かる）。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        write_run_info(self._report(), self.directory, "running", self.pending)

    def add(self, run: MetricRun) -> None:
        """完了した metric を書き出す（``run_metrics`` の ``on_result`` に渡す）。

        書く前に、その metric の以前の実行の表を消す（成功した metric は今回書く形式、失敗した
        metric は csv / parquet のすべて）。成功した metric は各テーブルと日付ごとの断面を
        parquet / csv で書く（失敗した metric に書くテーブルはない）。以前の ``add()`` で
        書けなかった metric があれば、ここでもう一度書く。そのうえで summary と run_info.json
        を、ファイルに書けた metric の内容に更新する。

        Args:
            run: 完了した metric の実行結果（成功・失敗とも）。

        Raises:
            Exception: テーブルの書き出しに失敗した場合（summary と run_info.json を更新した
                後で、最初の例外を送出する）。``run_metrics`` はこれをログに出して実行を続け、
                書けなかった metric は ``pending`` のまま残る。
        """
        self._done[run.key] = run
        files = [fmt for fmt in self.formats if fmt != "xlsx"]
        error: Exception | None = None
        for key, done in self._done.items():
            if key in self._written:
                continue
            try:
                clear_metric_files(self.directory, key, table_suffixes(files, done.ok))
                if done.ok:
                    single = EvaluationReport(runs={key: done})
                    for fmt in files:
                        write_tables(single, self.directory, fmt)
                    if self.by_date:
                        write_by_date(single, self.directory, files, self.by_date, warn=False)
                self._written.add(key)
            except Exception as e:  # 1つ書けなくても、他の metric の書き出しは続ける
                error = error or e
        report = self._report()
        for fmt in files:
            write_summary(report, self.directory, fmt)
        write_run_info(report, self.directory, "running", self.pending)
        if error is not None:
            raise error

    def finish(self, report: EvaluationReport) -> Path:
        """全 metric が終わった後、残りを書いて ``state: finished`` にする。

        xlsx はここで初めて書く。``add()`` で書けた metric の parquet / csv は書き直さず、
        書けなかった metric の分だけを書く。最終的な出力は ``report.save()`` と同じになる。

        Args:
            report: 全 metric の結果を持つレポート。

        Returns:
            出力先ディレクトリの Path。

        Raises:
            Exception: 書き出しに失敗した場合（``api.evaluate`` が ``abort()`` を呼び、
                run_info.json は ``state: aborted`` になる）。
        """
        self._done.update(report.runs)
        written = {key for key in self._written if self._done[key].ok}
        out = write_report(report, self.directory, self.formats, self.by_date, skip=written)
        self._written.update(report.runs)
        return out

    def abort(self) -> None:
        """中断したときに、ここまでに終わった metric の分を書いて ``state: aborted`` にする。

        計算が終わった metric があれば ``save()`` と同じ形（xlsx を含む）で書き、なければ
        run_info.json だけを更新する。その書き出しにも失敗した場合（または Ctrl-C で
        さらに中断された場合）は、ファイルに書けた metric だけを載せた run_info.json を
        ``state: aborted`` で書く。元の例外を隠さないよう、ここでの書き出しの失敗は
        ERROR のログを出すだけで送出しない（Ctrl-C などはそのまま送出する）。
        """
        try:
            report = self._report(written_only=False)
            not_computed = [key for key in self.keys if key not in self._done]
            if report.runs:
                written = {key for key in self._written if self._done[key].ok}
                write_report(
                    report,
                    self.directory,
                    self.formats,
                    self.by_date,
                    "aborted",
                    not_computed,
                    skip=written,
                )
            else:
                self.directory.mkdir(parents=True, exist_ok=True)
                write_run_info(report, self.directory, "aborted", not_computed)
        except BaseException as error:
            try:
                write_run_info(self._report(), self.directory, "aborted", self.pending)
            except Exception:
                logger.exception("Failed to update run_info.json in %s", self.directory)
            if not isinstance(error, Exception):
                raise
            logger.error(
                "Failed to write the partial results to %s", self.directory, exc_info=error
            )
