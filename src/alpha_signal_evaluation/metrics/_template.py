"""新しい metric のコピー元（``_`` 始まりなので自動探索の対象外）。

使い方:
    1. このファイルを ``metrics/<あなたの metric>.py`` にコピーする（``_`` で始めない）。
       ``metrics/`` 直下の ``_`` で始まらないモジュールは自動で import され、
       ``@register_metric`` によって登録される。外部パッケージで配布する場合は
       entry points group ``alpha_signal_evaluation.metrics`` に登録する
       （``registry`` モジュールを参照）。
    2. クラス名・``name``・``description``・``requires``・``Params``・``compute`` を書き換える。
       ``name`` は他の metric と重複してはいけない（登録時に ``ValueError``）。
    3. config.yaml の ``metrics`` に ``- name: <name>``（必要なら ``params:``）を追加する。
    4. ``uv run pytest tests/metrics/test_contract.py`` で契約テストが自動で走る。
       検証内容: ``name`` の一致、``description`` が空でないこと、``Params()`` が
       デフォルトだけで作れること、未知のパラメータが拒否されること、``requires`` に
       制限したデータで ``compute`` が ``MetricResult`` を返すこと（tables か summary が
       空でなく、表は DataFrame、summary の値は float）、``check`` が利用可能なデータに
       対してエラーを返さないこと。

守るルール:
    - DataBundle を受け取り MetricResult を返すだけ。データ取得・ファイル出力はしない
    - 時点をずらさない。シグナルとリターンの組は ``data.aligned(signal, returns, horizon)``
      で取り出す（同じ行は pipeline で時点合わせ済み）
    - 欠損を 0 で埋めない
    - ``requires`` に宣言したデータ以外を使わない

パラメータの意味の検証（例: 存在しない列名の指定）を計算前に行いたい場合は
``Metric.check`` を上書きし、``super().check(params, spec)`` の結果にメッセージを追加して返す。
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
    """``my_metric`` のパラメータ（例）。

    すべてのフィールドにデフォルト値を付けること（config で params を省略できるように）。
    値域は pydantic の ``Field(ge=...)`` 等で宣言すると、計算前に検証される。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名。デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        min_obs: 日付ごとの平均を計算する最小銘柄数（1 以上）。デフォルト 10。
    """

    min_obs: int = Field(default=10, ge=1)


@register_metric  # ← コピーしたら有効になる
class MyMetric(Metric):
    """（何を測る metric かを1文で書く。例: シグナルが正の銘柄の平均フォワードリターン。）

    例: シグナルが正の銘柄の、フォワードリターンの日付ごとの等ウェイト平均を求め、
    その時系列平均を出す。シグナルが正の銘柄が ``min_obs`` 未満の日付は除く。

    クラス docstring には、何をどう測るか、各パラメータ（``Params``）の意味、
    出力する表の名前と列、summary のキーの形式を書く。

    出力:
        tables["summary"]: 1行が (signal, returns, horizon) の1組。
            columns=[signal, returns, horizon, mean_return]。
        summary: ``"{signal}/{returns}/h{horizon}/mean_return"`` → 平均リターン。
    """

    name = "my_metric"  # config.yaml で参照する名前。他と重複しないこと
    description = "Example: average forward return of assets with a positive signal"
    requires = frozenset({"signals", "forward_returns"})
    Params = MyMetricParams

    def compute(self, data: DataBundle) -> MetricResult:
        """シグナル × リターン系列 × ホライズンの組ごとに計算する。

        省略されたパラメータは ``data.resolve_*`` で bundle にある全てに解決し、
        ``data.aligned`` で時点合わせ済みのシグナルとフォワードリターンの組を取り出す。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。

        Returns:
            ``tables={"summary": ...}`` と summary 辞書を持つ MetricResult。
        """
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
