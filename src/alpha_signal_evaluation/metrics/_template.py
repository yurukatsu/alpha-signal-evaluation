"""新しい metric のコピー元（``_`` 始まりなので自動探索の対象外）。

使い方:
    1. このファイルを ``metrics/<あなたの metric>.py`` にコピーする（``_`` で始めない）
    2. クラス名・``name``・``description``・``requires``・``Params``・``compute`` を書き換える
    3. config.yaml の ``metrics`` に ``- name: <name>`` を追加する
    4. ``uv run pytest tests/metrics/test_contract.py`` で契約テストが自動で走る

守るルール:
    - DataBundle を受け取り MetricResult を返すだけ。データ取得・ファイル出力はしない
    - 時点をずらさない。シグナルとリターンの組は ``data.aligned(signal, returns, horizon)``
      で取り出す（同じ行は pipeline で時点合わせ済み）
    - 欠損を 0 で埋めない
    - ``requires`` に宣言したデータ以外を使わない
"""

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


# パラメータは pydantic で定義する。config.yaml の params がここで検証される。
# signals / returns / horizons は SignalReturnParams が持ち、存在チェックも自動で行われる
class MyMetricParams(SignalReturnParams):
    min_obs: int = Field(default=10, ge=1)


@register_metric  # ← コピーしたら有効になる
class MyMetric(Metric):
    """（何を測る metric かを書く）"""

    name = "my_metric"  # config.yaml で参照する名前。他と重複しないこと
    description = "Example: average forward return of assets with a positive signal"
    requires = frozenset({"signals", "forward_returns"})
    Params = MyMetricParams

    def compute(self, data: DataBundle) -> MetricResult:
        p = self.params
        rows = []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                for h in data.resolve_horizons(p.horizons):
                    # index=(date, asset_id)、columns=[signal, "forward_return"]
                    df = data.aligned(signal, ret, h)
                    positive = df[df[signal] > 0]
                    by_date = positive.groupby(level=DATE)["forward_return"]
                    mean = by_date.mean().where(by_date.count() >= p.min_obs)
                    rows.append(
                        {
                            "signal": signal,
                            "returns": ret,
                            "horizon": h,
                            "mean_return": mean.mean(),
                        }
                    )
        table = pd.DataFrame(rows)
        return MetricResult(
            tables={"summary": table},  # 出力したい表（名前 -> DataFrame）
            summary={  # 1つの数値で表せる要約（キー -> float）
                f"{r['signal']}/{r['returns']}/h{r['horizon']}/mean_return": r["mean_return"]
                for r in rows
            },
        )
