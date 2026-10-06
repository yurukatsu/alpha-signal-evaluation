"""時点合わせ（フォワードリターン・期間集約）のテスト。1期ずれを検出するための最重要テスト。"""

import numpy as np
import pandas as pd
import pytest

from alpha_signal_evaluation.pipeline.preprocess import (
    forward_returns,
    to_period_returns,
)

KEYS = ["20200131", "20200229", "20200331", "20200430", "20200531", "20200630"]


def _period(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"A": values}, index=pd.Index(KEYS, name="date"))


class TestForwardReturns:
    def test_realized_h1_is_next_row(self):
        period = _period([np.nan, 0.01, 0.02, 0.03, 0.04, 0.05])
        fwd = forward_returns(period, [1], "realized", "sum")[1]["A"]
        # 行 t のシグナルに対し行 t+1 の実現リターン
        assert fwd.tolist()[:5] == pytest.approx([0.01, 0.02, 0.03, 0.04, 0.05])
        assert np.isnan(fwd.iloc[-1])

    def test_forward_h1_is_same_row(self):
        period = _period([0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
        fwd = forward_returns(period, [1], "forward", "sum")[1]["A"]
        assert fwd.tolist() == pytest.approx([0.01, 0.02, 0.03, 0.04, 0.05, 0.06])

    def test_realized_multi_horizon_sum(self):
        period = _period([np.nan, 0.01, 0.02, 0.03, 0.04, 0.05])
        fwd = forward_returns(period, [3], "realized", "sum")[3]["A"]
        # 行 t+1 〜 t+3
        assert fwd.iloc[0] == pytest.approx(0.01 + 0.02 + 0.03)
        assert fwd.iloc[2] == pytest.approx(0.03 + 0.04 + 0.05)
        assert fwd.iloc[3:].isna().all()

    def test_forward_multi_horizon_compound(self):
        period = _period([0.1, 0.2, -0.1, 0.0, 0.05, 0.02])
        fwd = forward_returns(period, [2], "forward", "compound")[2]["A"]
        # 行 t 〜 t+1 を複利で束ねる
        assert fwd.iloc[0] == pytest.approx(1.1 * 1.2 - 1)
        assert fwd.iloc[1] == pytest.approx(1.2 * 0.9 - 1)
        assert np.isnan(fwd.iloc[-1])

    def test_missing_period_stays_missing(self):
        period = _period([np.nan, 0.01, np.nan, 0.03, 0.04, 0.05])
        fwd = forward_returns(period, [2], "realized", "sum")[2]["A"]
        # 行 0 は行 1・2 を使うが行 2 が欠損 → 0 埋めせず欠損
        assert np.isnan(fwd.iloc[0])
        assert np.isnan(fwd.iloc[1])
        assert fwd.iloc[2] == pytest.approx(0.07)

    def test_signal_predicting_next_period_gives_perfect_alignment(self):
        rng = np.random.default_rng(0)
        signal = pd.DataFrame(rng.standard_normal((6, 20)), index=pd.Index(KEYS, name="date"))
        # realized: 行 t のリターンは行 t-1 のシグナルで決まる
        period = signal.shift(1)
        fwd = forward_returns(period, [1], "realized", "sum")[1]
        pd.testing.assert_frame_equal(fwd.iloc[:-1], signal.iloc[:-1])


class TestPeriodReturns:
    DAYS = [
        "20200128",
        "20200131",
        "20200203",
        "20200214",
        "20200228",
        "20200302",
        "20200331",
    ]
    BOUNDS = pd.Series(
        ["20200131", "20200228", "20200331"],
        index=pd.Index(["20200131", "20200229", "20200331"], name="date"),
    )

    def _daily(self, values):
        return pd.DataFrame({"A": values}, index=pd.Index(self.DAYS, name="date"))

    def test_realized_buckets(self):
        daily = self._daily([0.5, 0.5, 0.01, 0.02, 0.03, 0.04, 0.05])
        out = to_period_returns(daily, self.BOUNDS, "realized", "sum")["A"]
        # 最初の行は起点がないため欠損（1/28・1/31 はどの期間にも入らない）
        assert np.isnan(out.iloc[0])
        # (1/31, 2/28] と (2/28, 3/31]
        assert out.iloc[1] == pytest.approx(0.01 + 0.02 + 0.03)
        assert out.iloc[2] == pytest.approx(0.04 + 0.05)

    def test_realized_compound(self):
        daily = self._daily([0.5, 0.5, 0.01, 0.02, 0.03, 0.04, 0.05])
        out = to_period_returns(daily, self.BOUNDS, "realized", "compound")["A"]
        assert out.iloc[1] == pytest.approx(1.01 * 1.02 * 1.03 - 1)

    def test_forward_buckets(self):
        daily = self._daily([0.5, 0.01, 0.02, 0.03, 0.04, 0.05, 0.9])
        out = to_period_returns(daily, self.BOUNDS, "forward", "sum")["A"]
        # [1/31, 2/28) と [2/28, 3/31)。最後の行は終点がないため欠損
        assert out.iloc[0] == pytest.approx(0.01 + 0.02 + 0.03)
        assert out.iloc[1] == pytest.approx(0.04 + 0.05)
        assert np.isnan(out.iloc[2])

    def test_all_missing_period_is_not_zero(self):
        daily = self._daily([0.5, 0.5, np.nan, np.nan, np.nan, 0.04, 0.05])
        out = to_period_returns(daily, self.BOUNDS, "realized", "compound")["A"]
        assert np.isnan(out.iloc[1])
        assert out.iloc[2] == pytest.approx(1.04 * 1.05 - 1)
