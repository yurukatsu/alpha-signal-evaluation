from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alpha_signal_evaluation.config import EvaluationConfig, load_config
from alpha_signal_evaluation.metrics.base import ReturnAggregation

MINIMAL = {
    "version": 1,
    "calendar": {"path": "calendar.tsv"},
    "data": {
        "signals": {
            "source": "file",
            "path": "signals/{Base_day}.parquet",
            "columns": {"asset_id": "code"},
            "values": ["value"],
        }
    },
    "returns": [
        {
            "source": "db",
            "connection": "risk_models",
            "query": "sql/returns.sql",
            "match_on": "Trading_day",
            "columns": {"date": "dt", "asset_id": "code"},
            "series": {
                "total": {"column": "ret", "kind": "total"},
                "specific": {"column": "sret", "kind": "specific"},
            },
        }
    ],
    "metrics": [{"name": "ic"}],
}


def _with(**overrides):
    return {**MINIMAL, **overrides}


def test_minimal_config():
    cfg = EvaluationConfig.model_validate(MINIMAL)
    assert cfg.data.signal_names == ["value"]
    assert cfg.return_kinds == {"total": "total", "specific": "specific"}
    assert cfg.horizons == [1]
    assert cfg.returns[0].convention == "realized"


def test_version_is_required():
    raw = dict(MINIMAL)
    del raw["version"]
    with pytest.raises(ValidationError, match="version"):
        EvaluationConfig.model_validate(raw)


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvaluationConfig.model_validate(_with(horizon=[1]))
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvaluationConfig.model_validate(_with(calendar={"path": "c.tsv", "idx": "Base_day"}))


def test_unknown_source_type_is_rejected():
    data = {"signals": {**MINIMAL["data"]["signals"], "source": "s3"}}
    with pytest.raises(ValidationError):
        EvaluationConfig.model_validate(_with(data=data))


def test_signals_accept_list():
    second = {**MINIMAL["data"]["signals"], "values": ["momentum"]}
    cfg = EvaluationConfig.model_validate(
        _with(data={"signals": [MINIMAL["data"]["signals"], second]})
    )
    assert cfg.data.signal_names == ["value", "momentum"]


def test_signal_values_can_rename_columns():
    s = {**MINIMAL["data"]["signals"], "values": {"ai_v1234": "score"}}
    t = {**MINIMAL["data"]["signals"], "values": {"dss": "score"}}
    cfg = EvaluationConfig.model_validate(_with(data={"signals": [s, t]}))
    assert cfg.data.signal_names == ["ai_v1234", "dss"]
    assert cfg.data.signals[0].value_map == {"ai_v1234": "score"}
    with pytest.raises(ValidationError, match="must not be empty"):
        EvaluationConfig.model_validate(_with(data={"signals": {**s, "values": {}}}))


def test_duplicate_signal_names_are_rejected():
    s = MINIMAL["data"]["signals"]
    with pytest.raises(ValidationError, match="Duplicate signal names"):
        EvaluationConfig.model_validate(_with(data={"signals": [s, s]}))


def test_period_dates_accept_int_and_check_order():
    cfg = EvaluationConfig.model_validate(_with(period={"start": 20100131, "end": "20200131"}))
    assert cfg.period.start == "20100131"
    with pytest.raises(ValidationError, match="after"):
        EvaluationConfig.model_validate(_with(period={"start": "20200131", "end": "20100131"}))
    with pytest.raises(ValidationError):
        EvaluationConfig.model_validate(_with(period={"start": "2010-01-31"}))


def test_horizons_are_sorted_and_validated():
    assert EvaluationConfig.model_validate(_with(horizons=[12, 1, 3])).horizons == [
        1,
        3,
        12,
    ]
    with pytest.raises(ValidationError):
        EvaluationConfig.model_validate(_with(horizons=[0, 1]))
    with pytest.raises(ValidationError):
        EvaluationConfig.model_validate(_with(horizons=[1, 1]))


def test_duplicate_metric_keys_require_id():
    with pytest.raises(ValidationError, match="Duplicate metric keys"):
        EvaluationConfig.model_validate(_with(metrics=[{"name": "quantile"}, {"name": "quantile"}]))
    cfg = EvaluationConfig.model_validate(
        _with(metrics=[{"name": "quantile"}, {"name": "quantile", "id": "quantile_q10"}])
    )
    assert [m.key for m in cfg.metrics] == ["quantile", "quantile_q10"]


def test_return_series_names_are_unique():
    with pytest.raises(ValidationError, match="Duplicate return series"):
        EvaluationConfig.model_validate(_with(returns=MINIMAL["returns"] * 2))


def test_db_returns_require_match_on():
    source = {k: v for k, v in MINIMAL["returns"][0].items() if k != "match_on"}
    with pytest.raises(ValidationError, match="match_on"):
        EvaluationConfig.model_validate(_with(returns=[source]))


def test_specific_return_must_exist():
    with pytest.raises(ValidationError, match="specific_return"):
        EvaluationConfig.model_validate(_with(risk_models={"barra": {"specific_return": "nope"}}))
    cfg = EvaluationConfig.model_validate(
        _with(risk_models={"barra": {"specific_return": "specific"}})
    )
    assert cfg.risk_models["barra"].specific_return == "specific"


def test_classification_one_hot():
    cls = {"source": "file", "path": "labels/{Label2}.csv", "one_hot": True}
    cfg = EvaluationConfig.model_validate(
        _with(data={"signals": MINIMAL["data"]["signals"], "classification": cls})
    )
    assert cfg.data.classification.one_hot


def test_portfolios_are_reserved():
    with pytest.raises(ValidationError, match="reserved"):
        EvaluationConfig.model_validate(_with(portfolios=[{"id": "x"}]))


def test_relative_paths_resolve_against_config_file(tmp_path: Path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    path = config_dir / "eval.yaml"
    path.write_text(yaml.safe_dump(MINIMAL))
    cfg = load_config(path)
    assert cfg.calendar.path == str(config_dir / "calendar.tsv")
    assert cfg.data.signals[0].path == str(config_dir / "signals/{Base_day}.parquet")
    assert cfg.output.dir == str(config_dir / "out")
    assert cfg.cache.dir == str(config_dir / ".cache")


def test_absolute_paths_are_kept(tmp_path: Path):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(_with(calendar={"path": "/data/calendar.tsv"})))
    assert load_config(path).calendar.path == "/data/calendar.tsv"


def test_return_aggregation_merges_defaults():
    agg = ReturnAggregation.model_validate({"cumulative": {"specific": "compound"}})
    assert agg.cumulative == {"total": "compound", "specific": "compound"}
    assert ReturnAggregation().cumulative == {"total": "compound", "specific": "sum"}
    with pytest.raises(ValidationError):
        ReturnAggregation.model_validate({"cumulative": {"specific": "log"}})


def test_full_reference_config_is_valid():
    """examples/config.full.yaml（全項目の例）がスキーマと metric のパラメータに合っていること。"""
    from alpha_signal_evaluation.validation import spec_from_config, validate_metrics

    path = Path(__file__).parents[1] / "examples" / "config.full.yaml"
    cfg = load_config(path)
    # enabled: false の metric も含めてパラメータを検証する
    assert validate_metrics(cfg.metrics, spec_from_config(cfg)) == []
    assert len(cfg.metrics) == 7 and len(cfg.enabled_metrics) == 6
