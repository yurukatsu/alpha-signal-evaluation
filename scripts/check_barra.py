"""社内環境で Barra ソース（``source: barra``）の接続と前提を確認するスクリプト。

本番 DB に接続して、data/barra.py の実装が置いている前提が実データで成り立つかを確かめる。
接続先のホスト名・認証情報は ``.env``（環境変数）から読む。

使い方（リポジトリのルートで実行）::

    uv run python scripts/check_barra.py connection
    uv run python scripts/check_barra.py assumptions --model GEM3 --bid JPNAAB1
    uv run python scripts/check_barra.py compare path/to/config.yaml

- ``connection``: risk_models（SQL Server）と fs_trfac_glb（PostgreSQL）から数行ずつ読めるか
- ``assumptions``: 為替 RATE の向き、_IDTY の TDATE、BID の対応、ファクター名の重複、
  エクスポージャーのある日付（as-of の基準にする _D_FCTRTN の日付と一致するか）
- ``compare``: config（``source: barra`` のリターンを含む）で DataBundle を作り、1つの
  (日付, 銘柄) のフォワードリターンを DB の日次値から手計算した値と突き合わせる
"""

import argparse
import time
from typing import get_args

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from alpha_signal_evaluation import load_config
from alpha_signal_evaluation.config import BarraModel, BarraReturnsSource
from alpha_signal_evaluation.data import barra
from alpha_signal_evaluation.data.loaders import LoadContext, execute_cached, period_end_day
from alpha_signal_evaluation.pipeline.preprocess import build_bundle

MODELS = list(get_args(BarraModel))


def query(ctx: LoadContext, connection: str, sql: str, params: dict | None = None):
    """SQL を実行する（キャッシュしない）。列名は小文字にする。"""
    df = execute_cached(connection, sql, params or {}, ctx, label="check_barra")
    return df.rename(columns=lambda c: str(c).lower())


def show(title: str, body) -> None:
    """見出し付きで表示する。"""
    text = body.to_string() if isinstance(body, pd.DataFrame | pd.Series) else str(body)
    print(f"\n=== {title} ===\n{text}")


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------


def check_connection(model: str) -> None:
    """両方の接続先から数行ずつ読む。"""
    with LoadContext(calendar=None) as ctx:
        show(
            f"{barra.BARRA_DB}: {model}_FAC",
            query(
                ctx,
                barra.BARRA_DB,
                f"SELECT TOP 5 FCD, FGROUP, FAC, MDL FROM RISK_MODELS.dbo.{model}_FAC",
            ),
        )
        show(
            f"{barra.ID_MAP_DB}: barraid_jp",
            query(
                ctx,
                barra.ID_MAP_DB,
                "SELECT date, nri_code, bid FROM public.barraid_jp ORDER BY date DESC LIMIT 5",
            ),
        )
    print("\nOK: 両方の接続先から読めた")


# ---------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------


def check_rate_direction(ctx: LoadContext, model: str) -> None:
    """RATE が「USD / 現地通貨 × 100」（JPY なら 0.6〜0.7 前後）かを確かめる。"""
    rates = query(
        ctx,
        barra.BARRA_DB,
        f"SELECT TOP 10 DATE, CUR, RATE FROM RISK_MODELS.dbo.{model}_D_RATE "
        "WHERE CUR IN ('JPY', 'USD') ORDER BY DATE DESC",
    )
    show(f"{model}_D_RATE（JPY / USD の直近）", rates)
    jpy = pd.to_numeric(rates.loc[rates["cur"].str.strip() == "JPY", "rate"])
    if jpy.empty:
        print("要確認: JPY の行がない")
    elif jpy.median() < 10:
        print(f"OK: JPY の RATE ≈ {jpy.median():.4g}（USD / 現地通貨 × 100。実装の前提どおり）")
    elif jpy.median() > 1000:
        print(
            f"NG: JPY の RATE ≈ {jpy.median():.4g}（現地通貨 / USD）。"
            "USD 換算の向きを逆にする必要がある"
        )
    else:
        print(f"要確認: JPY の RATE ≈ {jpy.median():.4g}（どちらの向きか判断できない）")


def check_currency_periods(ctx: LoadContext, model: str, bid: str) -> None:
    """_IDTY の FDATE / TDATE の書き方（現在有効な行の TDATE）を表示する。"""
    idty = query(
        ctx,
        barra.BARRA_DB,
        f"SELECT BID, CUR, FDATE, TDATE FROM RISK_MODELS.dbo.{model}_IDTY WHERE BID = :bid",
        {"bid": bid},
    )
    show(f"{model}_IDTY（BID = {bid}）", idty)
    print(
        "確認: 現在有効な行の TDATE が NULL か遠い日付（99991231 等）なら実装の前提どおり。"
        "0 などの値なら対応が必要"
    )


def check_bid_mapping(ctx: LoadContext, model: str, bid: str) -> None:
    """barraid_jp の bid と _D_PRC の BID が同じコードで引けるかを確かめる。"""
    prc = query(
        ctx,
        barra.BARRA_DB,
        f"SELECT TOP 3 DATE, BID, DRTN FROM RISK_MODELS.dbo.{model}_D_PRC "
        "WHERE BID = :bid ORDER BY DATE DESC",
        {"bid": bid},
    )
    ids = query(
        ctx,
        barra.ID_MAP_DB,
        "SELECT date, nri_code, bid FROM public.barraid_jp WHERE TRIM(bid) = :bid "
        "ORDER BY date DESC LIMIT 3",
        {"bid": bid},
    )
    show(f"{model}_D_PRC（BID = {bid}）", prc)
    show(f"barraid_jp（bid = {bid}）", ids)
    if prc.empty or ids.empty:
        print("NG: どちらかで BID が見つからない（コード体系が違う可能性）")
    else:
        print("OK: 同じ BID で両方から引けた")


def check_factor_list(ctx: LoadContext, model: str) -> None:
    """ファクター名（接頭辞を除いたもの）とグループを表示し、重複を確かめる。"""
    warnings: list[str] = []
    factors = barra.load_factor_list(model, ctx, warnings)
    sizes = pd.Series({g: len(f) for g, f in factors.groups.items()}, name="n_factors")
    show(f"{model}_FAC のグループ", sizes)
    show("ファクター名（先頭10件）", ", ".join(factors.order[:10]))
    if warnings:
        print("要確認:", *warnings, sep="\n  ")
    else:
        print("OK: ファクター名の重複なし")


def check_exposure_dates(ctx: LoadContext, model: str) -> None:
    """直近の _D_FCTRTN の日付に _D_EXP のデータがあるか（as-of の基準日として使えるか）。"""
    dates = query(
        ctx,
        barra.BARRA_DB,
        f"SELECT DISTINCT TOP 5 DATE FROM RISK_MODELS.dbo.{model}_D_FCTRTN ORDER BY DATE DESC",
    )["date"].tolist()
    holders = ", ".join(f":d{i}" for i in range(len(dates)))
    counts = query(
        ctx,
        barra.BARRA_DB,
        f"SELECT DATE, COUNT(*) AS n FROM RISK_MODELS.dbo.{model}_D_EXP "
        f"WHERE DATE IN ({holders}) GROUP BY DATE ORDER BY DATE DESC",
        {f"d{i}": d for i, d in enumerate(dates)},
    )
    show(f"{model}_D_FCTRTN の直近の日付ごとの {model}_D_EXP の行数", counts)
    if len(counts) == len(dates):
        print("OK: ファクターリターンの日付にエクスポージャーがある")
    else:
        print("NG: エクスポージャーのない日付がある（as-of の基準日の決め方を見直す必要がある）")


def check_assumptions(model: str, bid: str) -> None:
    """実装の前提をまとめて確かめる。"""
    with LoadContext(calendar=None) as ctx:
        check_rate_direction(ctx, model)
        check_currency_periods(ctx, model, bid)
        check_bid_mapping(ctx, model, bid)
        check_factor_list(ctx, model)
        check_exposure_dates(ctx, model)


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def compare_with_raw(config_path: str, series: str | None) -> None:
    """パッケージのフォワードリターンと、DB の日次値から手計算した値を突き合わせる。

    最短ホライズンで、値のある (日付, 銘柄) のうち真ん中のものを1つ選ぶ。realized 規約で、
    行 t のフォワードリターンは (match_on[t], match_on[t + h]] の日次値を束ねたもの。
    """
    cfg = load_config(config_path)
    sources = [s for s in cfg.returns if isinstance(s, BarraReturnsSource)]
    if series is not None:
        sources = [s for s in sources if series in s.series]
    if not sources:
        raise SystemExit("config に source: barra のリターン（指定した系列）がない")
    source = sources[0]
    name = series or next(iter(source.series))
    kind = source.series[name].kind
    if source.convention != "realized":
        raise SystemExit("convention: realized のソースだけを確認できる")

    started = time.perf_counter()
    prepared = build_bundle(cfg)
    print(f"build_bundle: {time.perf_counter() - started:.1f} 秒")
    if prepared.warnings:
        show("警告", "\n".join(prepared.warnings))

    h = min(cfg.horizons)
    values = prepared.bundle.forward_returns[name][h].stack().dropna()
    if values.empty:
        raise SystemExit(f"{name!r} の h={h} のフォワードリターンが1件もない")
    key, asset = values.index[len(values) // 2]
    calendar = prepared.plan.calendar
    pos = calendar.keys.tolist().index(key)
    bounds = [period_end_day(calendar.frame[source.match_on].iloc[p]) for p in (pos, pos + h)]

    with LoadContext(calendar) as ctx:
        id_map = barra.load_id_map([bounds[1]], ctx, [])
        bids = id_map.loc[id_map["asset_id"] == asset, "bid"].unique()
        if len(bids) != 1:
            raise SystemExit(f"{asset} の BID が1つに決まらない: {list(bids)}")
        table, column = {"total": ("D_PRC", "DRTN"), "specific": ("D_SRTN", "SRTN")}[kind]
        raw = query(
            ctx,
            barra.BARRA_DB,
            f"SELECT DATE, {column} AS v FROM RISK_MODELS.dbo.{source.model}_{table} "
            "WHERE BID = :bid AND DATE > :start AND DATE <= :end ORDER BY DATE",
            {"bid": bids[0], "start": int(bounds[0]), "end": int(bounds[1])},
        )
    daily = pd.to_numeric(raw["v"]).dropna() / 100
    manual = float(np.prod(1 + daily) - 1) if kind == "total" else float(daily.sum())
    got = float(values.loc[(key, asset)])

    show(
        "突き合わせ",
        pd.Series(
            {
                "系列": f"{name}（{kind}, currency={source.currency}）",
                "日付（calendar.index）": key,
                "銘柄（nri_code / BID）": f"{asset} / {bids[0]}",
                "期間": f"({bounds[0]}, {bounds[1]}]、{len(daily)} 日",
                "パッケージ": f"{got:.8f}",
                "手計算": f"{manual:.8f}",
                "差": f"{got - manual:.2e}",
            }
        ),
    )
    if kind == "total" and source.currency == "USD":
        print(
            "注意: USD 建ては手計算に為替を入れていないので一致しない（currency: local で確認する）"
        )
    elif abs(got - manual) < 1e-10:
        print("OK: 一致した")
    else:
        print("NG: 一致しない")


def main() -> None:
    """コマンドライン引数を読んで確認を実行する。"""
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("connection", help="両方の接続先から読めるか")
    p.add_argument("--model", choices=MODELS, default="JPE4")
    p = sub.add_parser("assumptions", help="実装の前提を実データで確かめる")
    p.add_argument("--model", choices=MODELS, default="GEM3")
    p.add_argument("--bid", default="JPNAAB1", help="確認に使う BID")
    p = sub.add_parser("compare", help="フォワードリターンを手計算と突き合わせる")
    p.add_argument("config", help="source: barra のリターンを含む config.yaml")
    p.add_argument("--series", help="確認する系列名（省略時は最初の Barra ソースの最初の系列）")
    args = parser.parse_args()

    if args.command == "connection":
        check_connection(args.model)
    elif args.command == "assumptions":
        check_assumptions(args.model, args.bid)
    else:
        compare_with_raw(args.config, args.series)


if __name__ == "__main__":
    main()
