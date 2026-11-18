"""テスト用の時間のかかる metric（並列実行の中断を確かめるため）。

ワーカープロセスからも import できるよう、テストモジュールではなく独立したモジュールに置く。
レジストリには登録しない（契約テストや ``metrics list`` の対象にしないため）。
"""

import time

from alpha_signal_evaluation.metrics.base import Metric, MetricParams, MetricResult


class SlowParams(MetricParams):
    """``SlowMetric`` のパラメータ。

    Attributes:
        seconds: ``compute`` の中で待つ秒数。
    """

    seconds: float = 1.0


class SlowMetric(Metric):
    """指定した秒数だけ待ってから、固定の summary を返す。"""

    name = "slow"
    description = "Sleeps for a while (tests only)"
    Params = SlowParams

    def compute(self, data) -> MetricResult:
        """``seconds`` 秒待って ``{"slept": seconds}`` を返す。"""
        time.sleep(self.params.seconds)
        return MetricResult(summary={"slept": float(self.params.seconds)})
