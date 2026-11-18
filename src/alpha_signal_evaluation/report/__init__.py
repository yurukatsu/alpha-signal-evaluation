"""report 層: 評価結果の保持と書き出し。

``EvaluationReport`` が metric ごとの実行結果（MetricRun）と警告を保持し、
``save()`` で parquet / csv / xlsx と run_info.json を書き出す。出力形式の処理は
``report.writers`` に閉じ込め、metrics や pipeline には持ち込まない。

結果は metric のテーブルごとの時系列に加えて、日付ごとの断面（``by_date()`` / ``at()``、
書き出しは ``output.by_date``）としても取り出せる。断面は ``date`` 列を持つテーブルを
ここで切り出すだけで、metric 側の対応は要らない。

``IncrementalWriter`` は metric が1つ終わるたびに書き出す（``api.evaluate(save=...)``）。
途中でプロセスが落ちても、それまでに終わった metric の結果が残る。
"""

from .incremental import IncrementalWriter
from .report import EvaluationReport

__all__ = ["EvaluationReport", "IncrementalWriter"]
