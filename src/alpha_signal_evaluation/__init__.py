"""株式アルファシグナルの評価。

シグナル（銘柄ごとのスコア）とフォワードリターンから、IC・分位分析・カテゴリ別分析・
アルファの減衰などの評価指標（metric）を計算するライブラリ。

処理は config.yaml を起点に、cli → api → validation / pipeline → data → io の順に流れる。

- ``config``: config.yaml のスキーマと構造の検証
- ``validation``: カレンダーや metric レジストリと突き合わせる意味の検証
- ``pipeline``: データの準備（時点合わせは ``pipeline/preprocess.py`` だけで行う）と
  metric の実行
- ``data`` / ``io``: カレンダー・ファイル・DB からの読み込み
- ``metrics``: ``DataBundle`` を受け取り ``MetricResult`` を返す評価指標
- ``report``: 結果の集約と書き出し

このモジュールは主要な公開 API を再エクスポートする。

- ``evaluate`` / ``validate``: config に従って評価・検証する
- ``load_config`` / ``EvaluationConfig``: config の読み込みとモデル
- ``make_bundle`` / ``DataBundle`` / ``RiskModelData``: 手元の DataFrame から
  時点合わせ済みのデータを作り、io をバイパスして評価する
- ``Metric`` / ``MetricParams`` / ``MetricResult`` / ``ReturnAggregation`` /
  ``register_metric``: 独自の metric を実装・登録する
- ``EvaluationReport`` / ``ConfigValidationError``: 結果と検証エラー

Python API::

    from alpha_signal_evaluation import evaluate

    report = evaluate("config.yaml")
    report["ic"].tables["summary"]
    report.save()

手元の DataFrame から評価する場合::

    from alpha_signal_evaluation import evaluate, make_bundle

    data = make_bundle(signals, {"total": period_returns},
                       return_kinds={"total": "total"}, horizons=[1])
    report = evaluate({"version": 1, "metrics": [{"name": "ic"}]}, data=data)
"""

from .api import evaluate, validate
from .config import EvaluationConfig, load_config
from .data.bundle import DataBundle, RiskModelData
from .metrics import (
    Metric,
    MetricParams,
    MetricResult,
    ReturnAggregation,
    register_metric,
)
from .pipeline.preprocess import make_bundle
from .report import EvaluationReport
from .validation import ConfigValidationError

__all__ = [
    "ConfigValidationError",
    "DataBundle",
    "EvaluationConfig",
    "EvaluationReport",
    "Metric",
    "MetricParams",
    "MetricResult",
    "ReturnAggregation",
    "RiskModelData",
    "evaluate",
    "load_config",
    "make_bundle",
    "register_metric",
    "validate",
]
