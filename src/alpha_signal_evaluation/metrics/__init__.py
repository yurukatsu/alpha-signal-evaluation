"""シグナル評価の metric（IC・分位分析など）とその拡張の仕組み。

各 metric は :class:`~alpha_signal_evaluation.metrics.base.Metric` のサブクラスで、
pipeline が用意した :class:`~alpha_signal_evaluation.data.bundle.DataBundle` を受け取り、
:class:`~alpha_signal_evaluation.metrics.base.MetricResult`（表・数値サマリー・注記）を返す。
metric はデータの結合と集計だけを行い、時点のずらしは一切しない。

同梱の metric:
    - ``ic``: シグナルとフォワードリターンのクロスセクション相関（:mod:`.ic`）
    - ``quantile``: 分位ポートフォリオのリターンと上位 − 下位スプレッド（:mod:`.quantile`）
    - ``category``: カテゴリ（業種等）内の IC とカテゴリ別の平均シグナル（:mod:`.category`）
    - ``alpha_decay``: ホライズン別の IC と分位スプレッド（:mod:`.decay`）
    - ``autocorr``: シグナルのクロスセクション自己相関・偏自己相関（:mod:`.autocorr`）
    - ``factor_correlation``: シグナル間・シグナル × エクスポージャーの相関（:mod:`.correlation`）

新しい metric の追加方法は ``_template.py`` と :mod:`.base`・:mod:`.registry` を参照。
ここでは拡張に必要な公開 API（基底クラス・パラメータ・登録/探索関数）を再エクスポートする。
"""

from .base import (
    CUMULATIVE_DEFAULT,
    Metric,
    MetricParams,
    MetricResult,
    ReturnAggregation,
    SignalReturnParams,
)
from .registry import discover_metrics, get_metric, list_metrics, register_metric

__all__ = [
    "CUMULATIVE_DEFAULT",
    "Metric",
    "MetricParams",
    "MetricResult",
    "ReturnAggregation",
    "SignalReturnParams",
    "discover_metrics",
    "get_metric",
    "list_metrics",
    "register_metric",
]
