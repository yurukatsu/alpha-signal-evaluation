import pytest

from alpha_signal_evaluation.metrics import (
    Metric,
    MetricResult,
    get_metric,
    register_metric,
)
from alpha_signal_evaluation.metrics.registry import _REGISTRY


def test_builtin_metrics_are_discovered():
    for name in [
        "ic",
        "quantile",
        "category",
        "alpha_decay",
        "autocorr",
        "factor_correlation",
    ]:
        assert get_metric(name).name == name


def test_template_is_not_discovered():
    with pytest.raises(ValueError, match="Unknown metric"):
        get_metric("my_metric")


def test_duplicate_name_is_rejected():
    class Another(Metric):
        name = "ic"

        def compute(self, data):
            return MetricResult()

    with pytest.raises(ValueError, match="already registered"):
        register_metric(Another)


def test_abstract_metric_is_rejected():
    class Incomplete(Metric):
        name = "incomplete"

    with pytest.raises(TypeError, match="compute"):
        register_metric(Incomplete)


def test_unknown_requirement_is_rejected():
    with pytest.raises(TypeError, match="requires"):

        class Bad(Metric):
            name = "bad"
            requires = frozenset({"signal"})

            def compute(self, data):
                return MetricResult()


def test_register_and_use_custom_metric(bundle):
    @register_metric
    class Coverage(Metric):
        name = "test_coverage"
        description = "number of assets"
        requires = frozenset({"signals"})

        def compute(self, data):
            return MetricResult(summary={"n": float(len(data.signals))})

    try:
        assert get_metric("test_coverage")().compute(bundle).summary["n"] == len(bundle.signals)
    finally:
        _REGISTRY.pop("test_coverage")


def test_params_from_kwargs_dict_or_instance():
    ic = get_metric("ic")
    assert ic(method="pearson").params.method == "pearson"
    assert ic({"method": "pearson"}).params.method == "pearson"
    assert ic(ic.Params(method="pearson")).params.method == "pearson"
    with pytest.raises(ValueError):
        ic(method="kendall")
