"""テスト用の合成データ。外部パッケージで metric を作る場合のテストにも使える。

from alpha_signal_evaluation.testing import make_synthetic_bundle
result = MyMetric().compute(make_synthetic_bundle())
"""

from dataclasses import replace

import numpy as np
import pandas as pd

from .data.bundle import DataBundle, RiskModelData
from .data.columns import ASSET_ID, CATEGORY, DATE
from .pipeline.preprocess import forward_returns, make_bundle


def make_synthetic_bundle(
    n_dates: int = 36,
    n_assets: int = 80,
    horizons: tuple[int, ...] = (1, 3),
    seed: int = 0,
    missing_rate: float = 0.05,
) -> DataBundle:
    """小さな合成 DataBundle。

    - シグナル ``alpha`` は次の行（realized 規約で行 t+1）のスペシフィックリターンを予測する
    - ``noise`` は無関係、``mixed`` は両者の平均
    - リターン系列 ``total``（kind=total）と ``specific``（kind=residual）
    - 業種分類（5業種）と、リスクモデル ``barra``（スタイル2つ・業種2つ）
    """
    rng = np.random.default_rng(seed)
    n_rows = n_dates + max(horizons)
    keys = pd.date_range("2015-01-31", periods=n_rows, freq="ME").strftime("%Y%m%d").tolist()
    assets = [f"A{i:04d}" for i in range(n_assets)]

    alpha = rng.standard_normal((n_rows, n_assets))
    noise = rng.standard_normal((n_rows, n_assets))
    size = rng.standard_normal((n_rows, n_assets))
    momentum = 0.5 * alpha + rng.standard_normal((n_rows, n_assets))
    sectors = np.array([f"S{i % 5}" for i in range(n_assets)])
    industry_a = (sectors <= "S1").astype(float)

    factor_ret = pd.DataFrame(
        0.01 * rng.standard_normal((n_rows, 4)),
        index=pd.Index(keys, name=DATE),
        columns=["size", "momentum", "ind_a", "ind_b"],
    )
    # realized 規約: 行 t のリターンは「行 t-1 → t」。行 t のシグナルが行 t+1 のリターンを予測する
    specific = np.full((n_rows, n_assets), np.nan)
    specific[1:] = 0.01 * alpha[:-1] + 0.03 * rng.standard_normal((n_rows - 1, n_assets))
    systematic = np.full((n_rows, n_assets), np.nan)
    systematic[1:] = (
        size[:-1] * factor_ret["size"].to_numpy()[1:, None]
        + momentum[:-1] * factor_ret["momentum"].to_numpy()[1:, None]
        + industry_a[None, :] * factor_ret["ind_a"].to_numpy()[1:, None]
        + (1 - industry_a)[None, :] * factor_ret["ind_b"].to_numpy()[1:, None]
    )

    def wide(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            values,
            index=pd.Index(keys, name=DATE),
            columns=pd.Index(assets, name=ASSET_ID),
        )

    def long(values: np.ndarray, name: str) -> pd.Series:
        return wide(values).iloc[:n_dates].stack().rename(name)

    signals = pd.concat(
        [
            long(alpha, "alpha"),
            long(noise, "noise"),
            long((alpha + noise) / 2, "mixed"),
        ],
        axis=1,
    )
    if missing_rate:
        signals = signals.mask(rng.random(signals.shape) < missing_rate)

    classification = pd.DataFrame(
        {CATEGORY: np.tile(sectors, n_dates)},
        index=pd.MultiIndex.from_product([keys[:n_dates], assets], names=[DATE, ASSET_ID]),
    )
    bundle = make_bundle(
        signals,
        {"total": wide(systematic + specific), "specific": wide(specific)},
        return_kinds={"total": "total", "specific": "residual"},
        horizons=list(horizons),
        classification=classification,
        dates=keys[:n_dates],
    )

    exposures = pd.concat(
        [
            long(size, "size"),
            long(momentum, "momentum"),
            long(np.tile(industry_a, (n_rows, 1)), "ind_a"),
            long(np.tile(1 - industry_a, (n_rows, 1)), "ind_b"),
        ],
        axis=1,
    )
    fwd_factor = forward_returns(factor_ret, list(horizons), "realized", "sum")
    risk_model = RiskModelData(
        exposures=exposures,
        forward_factor_returns={
            h: f.reindex(pd.Index(keys[:n_dates], name=DATE)) for h, f in fwd_factor.items()
        },
        specific_return="specific",
        factor_groups={"style": ["size", "momentum"], "industry": ["ind_a", "ind_b"]},
    )
    return replace(bundle, risk_models={"barra": risk_model})
