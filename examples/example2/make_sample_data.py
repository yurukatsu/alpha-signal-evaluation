"""config.example.yaml 用のサンプルデータを examples/example2/input に作る。

月次（ファイル名が YYYYMM）・BARRA 形式の ID のデータと同じ形式の合成データを作る。
値はすべて乱数で、実在の銘柄・スコアとは無関係。

    uv run python examples/example2/make_sample_data.py
    uv run alpha-eval run examples/example2/config.example.yaml

手元の実データ（alpha / bm / univ / calendar.dat）で試す場合は、それらを input に置いて
``--keep-data`` を付ける。実データには触れず、合成リターン（純粋な乱数）と、
calendar.dat がなければそれだけを作る（本番の calendar.dat は上書きしない）。

    uv run python examples/example2/make_sample_data.py --keep-data

作るファイル:
    input/calendar.dat                カレンダー（本番と同じ形式。コメント行 + ヘッダー + 月次の行）
    input/alpha/{Base_month}.dat      シグナル（ヘッダー "#bid comp_ew"、スペース区切り）
    input/bm/{Base_month}.dat         ベンチマークウェイト（"# ..." のコメント行 + スペース区切り）
    input/univ/{Base_month}.dat       ユニバース・ウェイト（bm と同じ形式）
    input/returns/{Base_month}.csv    リターン（サンプル専用。その月の realized 月次リターン）
"""

import argparse
import string
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).parent / "input"
START, ALPHA_START, END = "2003-01", "2008-01", "2026-07"  # データを作る期間
CALENDAR_START, CALENDAR_END = "1987-12", "2049-12"  # 本番と同じく将来の月まで持つ
N_IDS = 900
BM_SIZE = 166


def make_calendar(start: str = CALENDAR_START, end: str = CALENDAR_END) -> pd.DataFrame:
    """本番の calendar.dat と同じ列構成の月次カレンダー。"""
    months = pd.period_range(start, end, freq="M")
    yyyymm = months.strftime("%Y%m")
    return pd.DataFrame(
        {
            "Base_month": yyyymm,
            "Monthend": yyyymm,
            "Trading_month": yyyymm,
            "Rskmdl_month": yyyymm,
            "Label1": yyyymm,
            # 西暦下2桁 + 月（1〜9, a〜c）。例: 2008-01 → "081"、2007-12 → "07c"
            "Label2": [f"{p.year % 100:02d}{'123456789abc'[p.month - 1]}" for p in months],
        }
    )


def write_calendar(calendar: pd.DataFrame) -> None:
    lines = ["# Rebalancing and trading monthly.", "#" + "\t".join(calendar.columns)]
    lines += ["\t".join(row) for row in calendar.itertuples(index=False)]
    (ROOT / "calendar.dat").write_text("\n".join(lines) + "\n")


def write_weights(path: Path, month: str, ids: np.ndarray, weights: np.ndarray) -> None:
    order = np.argsort(-weights)  # ウェイトの大きい順
    with path.open("w") as f:
        f.write(f"# format = weight\n# date = {month}\n")
        f.writelines(f"{ids[i]} {weights[i]:.15E}\n" for i in order)


def write_returns(month: str, ids: np.ndarray, total: np.ndarray, specific: np.ndarray) -> None:
    pd.DataFrame({"bid": ids, "ret": total, "sret": specific}).to_csv(
        ROOT / "returns" / f"{month}.csv", index=False
    )


def generate_all(rng: np.random.Generator) -> None:
    write_calendar(make_calendar())
    months = pd.period_range(START, END, freq="M").strftime("%Y%m")
    n_rows = len(months)
    letters = np.array(list(string.ascii_uppercase))
    ids = np.array(
        sorted(
            {
                f"IND{''.join(rng.choice(letters, 3))}{rng.choice([1, 1, 1, 2])}"
                for _ in range(N_IDS)
            }
        )
    )
    n = len(ids)

    # 上場・除外でユニバースが少しずつ入れ替わる
    first = rng.integers(-n_rows, n_rows // 2, n)
    last = first + rng.integers(n_rows, 3 * n_rows, n)
    cap = np.exp(rng.normal(0, 1.5, n))
    alpha = np.zeros((n_rows, n))
    alpha[0] = rng.standard_normal(n)
    for t in range(1, n_rows):
        alpha[t] = 0.6 * alpha[t - 1] + 0.8 * rng.standard_normal(n)

    # realized: 行 t のリターンは「行 t-1 → t」。行 t-1 のシグナルが行 t のリターンを予測する
    market = 0.008 + 0.06 * rng.standard_normal(n_rows)
    specific = np.full((n_rows, n), np.nan)
    specific[1:] = 0.006 * alpha[:-1] + 0.08 * rng.standard_normal((n_rows - 1, n))
    total = market[:, None] + specific

    for t, month in enumerate(months):
        univ = (first <= t) & (t <= last)
        cap_t = cap[univ] * np.exp(0.1 * rng.standard_normal(univ.sum()))
        write_weights(ROOT / "univ" / f"{month}.dat", month, ids[univ], cap_t / cap_t.sum())
        top = np.argsort(-cap_t)[:BM_SIZE]
        bm_w = cap_t[top] / cap_t[top].sum()
        write_weights(ROOT / "bm" / f"{month}.dat", month, ids[univ][top], bm_w)
        write_returns(month, ids[univ], total[t, univ], specific[t, univ])

        if month >= ALPHA_START.replace("-", ""):
            covered = univ & (rng.random(n) < 0.997)
            # 日付ごとに順位を正規分位点へ変換したスコア（平均 0・対称）
            rank = pd.Series(alpha[t, covered]).rank().to_numpy()
            score = norm.ppf(rank / (covered.sum() + 1))
            lines = ["#bid comp_ew"] + [
                f"{i} {float(s)!r}" for i, s in zip(ids[covered], score, strict=True)
            ]
            (ROOT / "alpha" / f"{month}.dat").write_text("\n".join(lines) + "\n")


def generate_calendar_and_returns(rng: np.random.Generator) -> None:
    """既存データを残し、合成リターン（シグナルとは無関係な乱数）と、なければカレンダーを作る。"""
    months = sorted(p.stem for p in (ROOT / "univ").glob("*.dat"))
    if not months:
        raise SystemExit(f"No univ files in {ROOT / 'univ'}")
    if (ROOT / "calendar.dat").exists():
        print(f"Keep existing {ROOT / 'calendar.dat'}")
    else:
        write_calendar(make_calendar())
    for month in months:
        path = ROOT / "univ" / f"{month}.dat"
        if not path.exists():
            continue
        ids = pd.read_csv(path, sep=r"\s+", comment="#", header=None, dtype=str)[0].to_numpy()
        specific = 0.08 * rng.standard_normal(len(ids))
        write_returns(month, ids, 0.008 + 0.06 * rng.standard_normal() + specific, specific)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="既存の alpha / bm / univ を残し、calendar.dat と合成リターンだけを作る",
    )
    args = parser.parse_args()

    rng = np.random.default_rng(7)
    for d in ["alpha", "bm", "univ", "returns"]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    if args.keep_data:
        generate_calendar_and_returns(rng)
    else:
        existing = [d for d in ["alpha", "bm", "univ"] if any((ROOT / d).iterdir())]
        if existing:
            raise SystemExit(
                f"{ROOT} already has data in {existing}. "
                "Use --keep-data to keep it, or remove it to generate synthetic data."
            )
        generate_all(rng)
    print(f"Sample data written to {ROOT}")


if __name__ == "__main__":
    main()
