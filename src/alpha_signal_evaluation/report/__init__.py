"""report 層: 評価結果の保持と書き出し。

``EvaluationReport`` が metric ごとの実行結果（MetricRun）と警告を保持し、
``save()`` で parquet / csv / xlsx と run_info.json を書き出す。出力形式の処理は
``report.writers`` に閉じ込め、metrics や pipeline には持ち込まない。
"""

from .report import EvaluationReport

__all__ = ["EvaluationReport"]
