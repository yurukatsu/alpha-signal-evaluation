"""config.example.yaml 用のサンプルデータを examples/example1/input に作る。

社内の入力データ（input.zip）と同じファイル形式の合成データを作る（ディレクトリ名はわかりやすく変えている）。
値はすべて乱数で、実在の銘柄・スコアとは無関係。

    uv run python examples/example1/make_sample_data.py
    uv run alpha-eval run examples/example1/config.example.yaml

作るファイル:
    input/calendar.dat            カレンダー（ヘッダー先頭 "#"、タブ区切り。末尾はスペース区切り）
    input/alpha/ai/{Label2}.csv   シグナル（ヘッダー "#nri_code,score"）
    input/alpha/dss/{Label2}.csv  シグナル（ヘッダーなし。値は -0.6 / -0.1 / 0.1 / 0.6）
    input/labels/{Label2}.csv     業種分類（ヘッダー "#nri_code,L001,...,L010" の one-hot）
    input/univ/{Trading_day}.dat  ユニバース（"# ..." のコメント行 + コード・ウェイトのタブ区切り）
    input/returns/{Base_day}.csv  リターン（本番は DB から取得。サンプル用の realized 月次リターン）
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent / "input"
N_ASSETS = 600
N_SECTORS = 10
UNIVERSE_RATE = 0.85


def make_calendar() -> pd.DataFrame:
    month_ends = pd.date_range("2018-01-31", "2026-07-31", freq="BME")
    trading = month_ends.strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "Base_day": trading,
            "Monthend_day": trading,
            "Trading_day": trading,
            "Rskmdl_month": (month_ends - pd.offsets.MonthEnd(1)).strftime("%Y%m"),
            "Label1": (month_ends - pd.offsets.MonthEnd(1)).strftime("%Y%m"),
            # シグナル等のファイルは Trading_day の前営業日（Label2）で作られる
            "Label2": (month_ends - pd.offsets.BDay(1)).strftime("%Y%m%d"),
        }
    )


def write_calendar(calendar: pd.DataFrame) -> None:
    lines = ["#" + "\t".join(calendar.columns)]
    for i, row in enumerate(calendar.itertuples(index=False)):
        # 本番のファイルと同じく、末尾の追記行はスペース区切りになっている
        sep = "    " if i >= len(calendar) - 3 else "\t"
        lines.append(sep.join(row))
    (ROOT / "calendar.dat").write_text("\n".join(lines) + "\n")


def make_codes(rng: np.random.Generator) -> np.ndarray:
    numeric = rng.choice(np.arange(1301, 9999), N_ASSETS - 20, replace=False)
    codes = [f"0{n:04d}" for n in numeric]
    codes += [f"0{n:03d}A" for n in rng.choice(np.arange(130, 999), 20, replace=False)]
    return np.array(sorted(codes))


def main() -> None:
    rng = np.random.default_rng(42)
    calendar = make_calendar()
    n_rows = len(calendar)
    codes = make_codes(rng)
    for d in ["alpha/ai", "alpha/dss", "labels", "univ", "returns"]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    write_calendar(calendar)

    # 業種（one-hot）。規模に偏りを持たせる
    sector_prob = rng.dirichlet(np.full(N_SECTORS, 2.0))
    sector = rng.choice(N_SECTORS, N_ASSETS, p=sector_prob)
    sector_names = [f"L{i + 1:03d}" for i in range(N_SECTORS)]
    one_hot = pd.DataFrame(np.eye(N_SECTORS, dtype=int)[sector], columns=sector_names)
    one_hot.insert(0, "#nri_code", codes)

    # シグナル: AI は持続性のある連続値、DSS は AI と相関する離散値
    ai = np.zeros((n_rows, N_ASSETS))
    ai[0] = rng.standard_normal(N_ASSETS)
    for t in range(1, n_rows):
        ai[t] = 0.7 * ai[t - 1] + np.sqrt(1 - 0.49) * rng.standard_normal(N_ASSETS)
    dss_latent = 0.5 * ai + np.sqrt(0.75) * rng.standard_normal((n_rows, N_ASSETS))

    # realized: 行 t のリターンは「行 t-1 → t」。行 t-1 のシグナルが行 t のリターンを予測する
    market = 0.006 + 0.045 * rng.standard_normal(n_rows)
    sector_ret = 0.02 * rng.standard_normal((n_rows, N_SECTORS))
    specific = np.full((n_rows, N_ASSETS), np.nan)
    specific[1:] = (
        0.005 * ai[:-1]
        + 0.003 * dss_latent[:-1]
        + 0.07 * rng.standard_normal((n_rows - 1, N_ASSETS))
    )
    total = market[:, None] + sector_ret[:, sector] + specific
    cap = np.exp(rng.normal(0, 1.5, N_ASSETS))

    for t, row in enumerate(calendar.itertuples(index=False)):
        univ = rng.random(N_ASSETS) < UNIVERSE_RATE
        weight = cap[univ] / cap[univ].sum()
        with (ROOT / "univ" / f"{row.Trading_day}.dat").open("w") as f:
            f.write(f"# format = weight\n# date = {row.Trading_day}\n")
            f.writelines(f"{c}\t{w:.6g}\n" for c, w in zip(codes[univ], weight, strict=True))

        score = ai[t, univ]
        pd.DataFrame(
            {"#nri_code": codes[univ], "score": (score - score.mean()) / score.std()}
        ).to_csv(ROOT / "alpha" / "ai" / f"{row.Label2}.csv", index=False)
        rank = pd.Series(dss_latent[t, univ]).rank(pct=True).to_numpy()
        dss = np.select([rank <= 0.07, rank <= 0.5, rank <= 0.93], [-0.6, -0.1, 0.1], 0.6)
        pd.DataFrame({"code": codes[univ], "dss": dss}).to_csv(
            ROOT / "alpha" / "dss" / f"{row.Label2}.csv", index=False, header=False
        )
        one_hot.to_csv(ROOT / "labels" / f"{row.Label2}.csv", index=False)
        pd.DataFrame({"nri_code": codes, "ret": total[t], "sret": specific[t]}).to_csv(
            ROOT / "returns" / f"{row.Base_day}.csv", index=False
        )
    print(f"Sample data written to {ROOT}")


if __name__ == "__main__":
    main()
