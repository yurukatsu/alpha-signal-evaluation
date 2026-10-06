"""株式アルファシグナルの評価。

Python API::

    from alpha_signal_evaluation import evaluate

    report = evaluate("config.yaml")
    report["ic"].tables["summary"]
    report.save()
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
