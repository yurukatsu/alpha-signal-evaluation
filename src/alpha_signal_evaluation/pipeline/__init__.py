"""pipeline 層: データの時点合わせ（preprocess）と metrics の実行（runner）。

- ``preprocess``: カレンダーに沿った評価行・延長行の決定、日次→期間リターンの集約、
  フォワードリターンの計算、ユニバース適用、DataBundle の組み立て。時点合わせは
  すべてここで行い、metrics には整列済みのデータだけを渡す。
- ``runner``: metrics を（必要ならプロセス並列で）実行し、metric ごとに失敗を隔離する。

io / data（読み込み）を使い、api から呼ばれる。report は runner の MetricRun を受け取る。
"""

from .preprocess import (
    PERIOD_AGGREGATION,
    EvaluationPlan,
    PreparedData,
    build_bundle,
    forward_returns,
    make_bundle,
    make_plan,
    to_period_returns,
)
from .runner import MetricRun, run_metrics

__all__ = [
    "PERIOD_AGGREGATION",
    "EvaluationPlan",
    "MetricRun",
    "PreparedData",
    "build_bundle",
    "forward_returns",
    "make_bundle",
    "make_plan",
    "run_metrics",
    "to_period_returns",
]
