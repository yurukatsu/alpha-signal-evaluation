import numpy as np
import pandas as pd
import pytest
import yaml

from alpha_signal_evaluation.testing import make_synthetic_bundle


@pytest.fixture(scope="session")
def bundle():
    return make_synthetic_bundle()


class Dataset:
    """ファイルベースの小さな評価データ一式を tmp_path に作る。

    シグナル ``perfect`` は次の行（realized 規約で t+1）のトータルリターンそのもの。
    時点合わせが正しければ h=1 の IC は全日付で 1 になる。
    """

    N_ROWS = 14
    N_ASSETS = 30

    def __init__(self, root):
        self.root = root
        rng = np.random.default_rng(0)
        months = pd.date_range("2019-12-31", periods=self.N_ROWS, freq="ME")
        self.base = months.strftime("%Y%m%d").tolist()
        self.trading = [pd.offsets.BMonthEnd().rollback(m).strftime("%Y%m%d") for m in months]
        self.rskmdl = (months - pd.offsets.MonthEnd(1)).strftime("%Y%m").tolist()
        # 同じ Rskmdl_month が続く行を作る（ファイルのキャッシュを確認するため）
        self.rskmdl[3] = self.rskmdl[2]
        self.codes = [f"{i:04d}" for i in range(1, self.N_ASSETS + 1)]  # 先頭ゼロあり

        header = "#Base_day\tMonthend_day\tTrading_day\tRskmdl_month\tLabel1\tLabel2\n"
        rows = [
            f"{b}\t{b}\t{t}\t{r}\t{r}\t{b}\n"
            for b, t, r in zip(self.base, self.trading, self.rskmdl, strict=True)
        ]
        (root / "calendar.tsv").write_text(header + "".join(rows))

        self.total = rng.normal(0, 0.05, (self.N_ROWS, self.N_ASSETS))
        self.total[0] = np.nan

    def perfect_signal(self, period_total: np.ndarray) -> np.ndarray:
        signal = np.full_like(period_total, np.nan)
        signal[:-1] = period_total[1:]
        return signal

    def write_signals(self, period_total: np.ndarray) -> None:
        signal = self.perfect_signal(period_total)
        rng = np.random.default_rng(1)
        (self.root / "signals").mkdir(exist_ok=True)
        for i, b in enumerate(self.base):
            pd.DataFrame(
                {
                    "code": self.codes,
                    "perfect": signal[i],
                    "noise": rng.standard_normal(self.N_ASSETS),
                }
            ).to_parquet(self.root / "signals" / f"{b}.parquet")

    def write_file_returns(self) -> None:
        (self.root / "returns").mkdir(exist_ok=True)
        for i, b in enumerate(self.base):
            pd.DataFrame(
                {"code": self.codes, "ret": self.total[i], "sret": self.total[i] * 0.5}
            ).to_csv(self.root / "returns" / f"{b}.csv", index=False)

    def write_classification(self) -> None:
        (self.root / "sector").mkdir(exist_ok=True)
        for r in dict.fromkeys(self.rskmdl):
            pd.DataFrame(
                {
                    "code": self.codes,
                    "gics_sector": [f"S{i % 3}" for i in range(self.N_ASSETS)],
                }
            ).to_csv(self.root / "sector" / f"{r}.csv", index=False)

    def write_universe(self) -> None:
        # 単一ファイル。日付は Trading_day（ハイフン区切り）で、銘柄 0001 は常に対象外
        rows = [
            {"dt": f"{t[:4]}-{t[4:6]}-{t[6:]}", "code": c}
            for t in self.trading
            for c in self.codes[1:]
        ]
        pd.DataFrame(rows).to_csv(self.root / "universe.csv", index=False)

    def config(self, **overrides) -> dict:
        cfg = {
            "version": 1,
            "calendar": {"path": "calendar.tsv", "index": "Base_day"},
            "period": {"start": self.base[1], "end": self.base[10]},
            "horizons": [1, 3],
            "data": {
                "signals": {
                    "source": "file",
                    "path": "signals/{Base_day}.parquet",
                    "columns": {"asset_id": "code"},
                    "values": ["perfect", "noise"],
                },
                "classification": {
                    "source": "file",
                    "path": "sector/{Rskmdl_month}.csv",
                    "columns": {"asset_id": "code", "category": "gics_sector"},
                },
            },
            "universe": {
                "source": "file",
                "path": "universe.csv",
                "match_on": "Trading_day",
                "columns": {"asset_id": "code", "date": "dt"},
            },
            "returns": [
                {
                    "source": "file",
                    "path": "returns/{Base_day}.csv",
                    "columns": {"asset_id": "code"},
                    "series": {
                        "total": {"column": "ret", "kind": "total"},
                        "specific": {"column": "sret", "kind": "residual"},
                    },
                }
            ],
            "metrics": [
                {"name": "ic", "params": {"method": "spearman", "min_obs": 5}},
                {"name": "quantile", "params": {"n_quantiles": 3}},
                {"name": "category", "params": {"min_obs": 3}},
            ],
            "output": {"dir": "out", "formats": ["parquet", "xlsx"]},
            "cache": {"dir": ".cache"},
        }
        cfg.update(overrides)
        return cfg

    def write_config(self, **overrides):
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(self.config(**overrides), sort_keys=False))
        return path


@pytest.fixture
def dataset(tmp_path):
    ds = Dataset(tmp_path)
    ds.write_signals(ds.total)
    ds.write_file_returns()
    ds.write_classification()
    ds.write_universe()
    return ds
