"""barra-internal-check ブランチの社内環境用 config（examples/*/config.barra.yaml）の検証。"""

from pathlib import Path

import pytest

from alpha_signal_evaluation import load_config
from alpha_signal_evaluation.config import BarraReturnsSource, BarraRiskModelConfig
from alpha_signal_evaluation.validation import spec_from_config, validate_metrics

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.mark.parametrize(
    ("example", "model", "asset_id"),
    [("example1", "JPE4", "nri_code"), ("example2", "GEMLT", "bid")],
)
def test_barra_config_is_valid(example, model, asset_id):
    cfg = load_config(EXAMPLES / example / "config.barra.yaml")
    (returns,) = cfg.returns
    assert isinstance(returns, BarraReturnsSource)
    assert (returns.model, returns.asset_id) == (model, asset_id)
    (risk_model,) = cfg.risk_models.values()
    assert isinstance(risk_model, BarraRiskModelConfig)
    assert risk_model.model == model
    assert validate_metrics(cfg.enabled_metrics, spec_from_config(cfg)) == []
    assert "factor_correlation" in [m.name for m in cfg.enabled_metrics]
