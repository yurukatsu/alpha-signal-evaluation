"""登録済みの全 metric に対する契約テスト。metrics/ にファイルを置くだけでテスト対象になる。"""

import pandas as pd
import pytest

from alpha_signal_evaluation.metrics import MetricResult, list_metrics
from alpha_signal_evaluation.metrics.base import MetricParams

METRICS = list_metrics()


@pytest.mark.parametrize("name", sorted(METRICS))
def test_returns_metric_result_with_only_required_data(name, bundle):
    metric_cls = METRICS[name]
    # requires に宣言していないデータは空にして渡す → 宣言外のデータに依存していれば失敗する
    result = metric_cls().compute(bundle.restrict(metric_cls.requires))
    assert isinstance(result, MetricResult)
    assert result.tables or result.summary
    for table_name, table in result.tables.items():
        assert isinstance(table_name, str)
        assert isinstance(table, pd.DataFrame)
    for key, value in result.summary.items():
        assert isinstance(key, str)
        assert isinstance(value, float)


@pytest.mark.parametrize("name", sorted(METRICS))
def test_declares_contract(name):
    metric_cls = METRICS[name]
    assert metric_cls.name == name
    assert metric_cls.description
    assert issubclass(metric_cls.Params, MetricParams)
    # パラメータはすべてデフォルト値を持つ（config で params を省略できるように）
    metric_cls.Params()
    # 未知のパラメータは拒否される
    with pytest.raises(ValueError):
        metric_cls.Params.model_validate({"__unknown__": 1})
    # CLI の metrics list で使う
    metric_cls.Params.model_json_schema()


@pytest.mark.parametrize("name", sorted(METRICS))
def test_check_passes_for_available_data(name, bundle):
    metric_cls = METRICS[name]
    assert metric_cls.check(metric_cls.Params(), bundle.spec()) == []
