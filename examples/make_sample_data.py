"""config.example.yaml 用のサンプルデータを examples/input に作る。

uv run python examples/make_sample_data.py
uv run alpha-eval run examples/config.example.yaml
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent / "input"
N_ROWS = 72
N_ASSETS = 300


def main() -> None:
    rng = np.random.default_rng(42)
    months = pd.date_range("2015-01-31", periods=N_ROWS, freq="ME")
    base = months.strftime("%Y%m%d").tolist()
    trading = [pd.offsets.BMonthEnd().rollback(m).strftime("%Y%m%d") for m in months]
    rskmdl = (months - pd.offsets.MonthEnd(1)).strftime("%Y%m").tolist()
    codes = [f"{1000 + i:04d}" for i in range(N_ASSETS)]
    sectors = [f"sector_{i % 10:02d}" for i in range(N_ASSETS)]

    ROOT.mkdir(exist_ok=True)
    header = "#Base_day\tMonthend_day\tTrading_day\tRskmdl_month\tLabel1\tLabel2\n"
    rows = [
        f"{b}\t{b}\t{t}\t{r}\t{r}\t{b}\n" for b, t, r in zip(base, trading, rskmdl, strict=True)
    ]
    (ROOT / "calendar.tsv").write_text(header + "".join(rows))

    # シグナル: value_score は翌月のスペシフィックリターンを弱く予測、momentum は持続性が高い
    value = rng.standard_normal((N_ROWS, N_ASSETS))
    momentum = np.zeros((N_ROWS, N_ASSETS))
    momentum[0] = rng.standard_normal(N_ASSETS)
    for t in range(1, N_ROWS):
        momentum[t] = 0.9 * momentum[t - 1] + np.sqrt(1 - 0.81) * rng.standard_normal(N_ASSETS)
    quality = rng.standard_normal((N_ROWS, N_ASSETS))
    size = rng.standard_normal((N_ROWS, N_ASSETS))

    # realized: 行 t のリターンは「行 t-1 → t」
    specific = np.full((N_ROWS, N_ASSETS), np.nan)
    specific[1:] = (
        0.004 * value[:-1]
        + 0.002 * momentum[:-1]
        + 0.06 * rng.standard_normal((N_ROWS - 1, N_ASSETS))
    )
    market = 0.005 + 0.04 * rng.standard_normal(N_ROWS)
    size_ret = 0.01 * rng.standard_normal(N_ROWS)
    total = (
        specific
        + market[:, None]
        + np.vstack([np.full(N_ASSETS, np.nan), size[:-1]]) * size_ret[:, None]
    )

    for d in ["signals", "returns", "sector", "barra"]:
        (ROOT / d).mkdir(exist_ok=True)
    for i, b in enumerate(base):
        pd.DataFrame(
            {
                "code": codes,
                "value_score": value[i],
                "momentum_12m": momentum[i],
                "quality": quality[i],
            }
        ).to_parquet(ROOT / "signals" / f"{b}.parquet", index=False)
        pd.DataFrame({"code": codes, "ret": total[i], "sret": specific[i]}).to_csv(
            ROOT / "returns" / f"{b}.csv", index=False
        )
    for i, r in enumerate(rskmdl):
        pd.DataFrame({"code": codes, "gics_sector": sectors}).to_csv(
            ROOT / "sector" / f"{r}.csv", index=False
        )
        pd.DataFrame({"code": codes, "SIZE": size[i], "MOMENTUM": momentum[i]}).to_csv(
            ROOT / "barra" / f"{r}.dat", sep="\t", index=False
        )

    # ユニバース: 単一ファイル（日付列は Trading_day）。各月 90% の銘柄を採用
    members = [
        {"dt": t, "code": c}
        for t in trading
        for c, keep in zip(codes, rng.random(N_ASSETS) < 0.9, strict=True)
        if keep
    ]
    pd.DataFrame(members).to_csv(ROOT / "universe.csv", index=False)
    print(f"Sample data written to {ROOT}")


if __name__ == "__main__":
    main()
