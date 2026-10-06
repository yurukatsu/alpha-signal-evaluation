"""評価結果 ``EvaluationReport``。結果の参照・集約・保存の入口。

``api.evaluate()`` が ``pipeline.runner.run_metrics()`` の結果（metric のキー ->
MetricRun）と警告から作る。失敗した metric があってもレポートは作られ、成否は
``ok`` / ``errors`` / ``status()`` で確認できる。ファイルへの書き出しの実装は
``writers.py`` にあり、ここでは出力先と形式の決定だけを行う。
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import EvaluationConfig, OutputFormat
from ..pipeline.runner import MetricRun


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

    def save(
        self,
        directory: str | Path | None = None,
        formats: list[OutputFormat] | None = None,
    ) -> Path:
        """結果を書き出す。省略時は config の output 設定を使う。

        ``directory`` と ``formats`` のうち省略した方だけを config の ``output.dir`` /
        ``output.formats`` で補う。書き出す内容（形式の重複は1回だけ扱う）:

        - parquet / csv: 成功した metric の各テーブルを ``<directory>/<key>/<table>.<ext>``、
          全 metric の summary を ``<directory>/summary.<ext>`` に書く。
          csv は UTF-8（BOM 付き）。
        - xlsx: ``<directory>/report.xlsx``（summary・status シートと各テーブルのシート）。
        - 形式によらず ``<directory>/run_info.json``（状態・エラー・警告・注記・config）。

        Args:
            directory: 出力先ディレクトリ（なければ親も含めて作成する）。
            formats: 出力形式（``parquet`` / ``csv`` / ``xlsx``）のリスト。

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
        return write_report(self, Path(directory), formats)


def _last_line(text: str | None) -> str:
    """テキスト（トレースバック）の前後の空白を除いた最終行を返す。None や空なら空文字。"""
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""
