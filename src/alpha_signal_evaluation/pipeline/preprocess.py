"""データ準備: カレンダー整列、期間リターン集約、フォワードリターン計算、ユニバース適用。

**時点合わせはすべてこのモジュールに閉じ込める。** metrics に届いた時点で、
同じ行のシグナルとフォワードリターンは整列済みであることを保証する。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import (
    Convention,
    DBSource,
    EvaluationConfig,
    FileSource,
    ReturnKind,
    ReturnsDBSource,
    RiskModelConfig,
)
from ..data.bundle import DataBundle, RiskModelData
from ..data.calendar import Calendar, check_increasing, load_calendar
from ..data.columns import ASSET_ID, CATEGORY, DATE, FACTOR, VALUE
from ..data.loaders import LoadContext, load_daily, load_snapshot, shift_day
from ..io.cache import ParquetCache

logger = logging.getLogger(__name__)

# 期間内（日次→期間）の集約。kind から固定で決まり、ユーザーには選ばせない
PERIOD_AGGREGATION: dict[ReturnKind, str] = {"total": "compound", "residual": "sum"}


# ---------------------------------------------------------------------------
# 評価対象の行
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationPlan:
    """評価期間の行と、リターン取得のために前後へ延長した行（位置はカレンダーの行番号）。"""

    calendar: Calendar
    eval_pos: np.ndarray
    horizons: list[int]

    @property
    def first(self) -> int:
        return int(self.eval_pos[0])

    @property
    def last(self) -> int:
        return int(self.eval_pos[-1])

    @property
    def anchor(self) -> int | None:
        """最初の評価行の1行前（realized の最初の期間の起点）。"""
        return self.first - 1 if self.first > 0 else None

    @property
    def ext_pos(self) -> np.ndarray:
        """期初の1行前から期末の最大ホライズン行先まで（カレンダーの範囲で切る）。"""
        start = self.first if self.anchor is None else self.anchor
        end = min(self.last + max(self.horizons), len(self.calendar) - 1)
        return np.arange(start, end + 1)

    @property
    def missing_tail(self) -> int:
        """カレンダーが足りずに取得できないホライズン分の行数。"""
        return max(self.last + max(self.horizons) - (len(self.calendar) - 1), 0)

    @property
    def eval_rows(self) -> pd.DataFrame:
        return self.calendar.rows(self.eval_pos)

    @property
    def ext_rows(self) -> pd.DataFrame:
        return self.calendar.rows(self.ext_pos)

    @property
    def eval_keys(self) -> list[str]:
        return self.eval_rows[self.calendar.index].tolist()

    @property
    def eval_index(self) -> pd.Index:
        return pd.Index(self.eval_keys, name=DATE)

    @property
    def ext_keys(self) -> list[str]:
        return self.ext_rows[self.calendar.index].tolist()

    @property
    def optional_keys(self) -> set[str]:
        """延長行のうち評価期間外の行（ファイルがなくても欠損として扱う）。"""
        return set(self.ext_keys) - set(self.eval_keys)


def make_plan(
    calendar: Calendar, start: str | None, end: str | None, horizons: list[int]
) -> EvaluationPlan:
    eval_pos = calendar.positions(start, end)
    if len(eval_pos) == 0:
        raise ValueError(
            f"No calendar rows in period start={start}, end={end} "
            f"(calendar {calendar.index}: {calendar.keys.iloc[0]}..{calendar.keys.iloc[-1]})"
        )
    return EvaluationPlan(calendar=calendar, eval_pos=eval_pos, horizons=sorted(horizons))


# ---------------------------------------------------------------------------
# リターンの時点合わせ
# ---------------------------------------------------------------------------


def to_period_returns(
    daily: pd.DataFrame,
    boundaries: pd.Series,
    convention: Convention,
    method: str,
) -> pd.DataFrame:
    """日次リターン（index=yyyymmdd, wide）をカレンダー期間に集約する。

    ``boundaries`` は index=calendar.index の値、values=期間の境界となる日付（例: Trading_day）。

    - realized: 行 i の期間は ``(boundaries[i-1], boundaries[i]]``。最初の行は起点がないため欠損
    - forward:  行 i の期間は ``[boundaries[i], boundaries[i+1])``。最後の行は終点がないため欠損

    ``method`` は ``compound``（複利）または ``sum``（加算）。期間内が全て欠損なら欠損のまま
    （0% にしない）。
    """
    edges = np.asarray(boundaries, dtype=str)
    days = np.asarray(daily.index, dtype=str)
    if convention == "realized":
        pos = np.searchsorted(edges, days, side="left")
        valid = (pos >= 1) & (pos < len(edges))
    else:
        pos = np.searchsorted(edges, days, side="right") - 1
        valid = (pos >= 0) & (pos < len(edges) - 1)
    x = daily[valid]
    keys = boundaries.index.to_numpy()[pos[valid]]
    if method == "compound":
        out = np.expm1(np.log1p(x).groupby(keys).sum(min_count=1))
    elif method == "sum":
        out = x.groupby(keys).sum(min_count=1)
    else:
        raise ValueError(f"Unknown aggregation method: {method!r}")
    out = out.reindex(boundaries.index)
    out.index.name = DATE
    return out


def forward_returns(
    period_ret: pd.DataFrame,
    horizons: list[int],
    convention: Convention,
    method: str,
) -> dict[int, pd.DataFrame]:
    """期間リターン（行=カレンダー行、欠けのない連続した行）からフォワードリターンを計算する。

    - realized: 行 t のリターンは「行 t-1 → t」の実現リターン。行 t のシグナルに対し
      行 t+1 〜 t+h のリターンを束ねる
    - forward:  行 t のリターンは「行 t → t+1」のリターン。行 t 〜 t+h-1 を束ねる

    途中に欠損がある場合は欠損のまま（min_periods=h）。
    """
    if method == "compound":
        x = np.log1p(period_ret)
    elif method == "sum":
        x = period_ret
    else:
        raise ValueError(f"Unknown aggregation method: {method!r}")
    offset = 0 if convention == "forward" else 1
    shifted = x.shift(-offset)
    out = {}
    for h in horizons:
        summed = shifted.rolling(h, min_periods=h).sum().shift(-(h - 1))
        out[h] = np.expm1(summed) if method == "compound" else summed
    return out


def _to_wide(df: pd.DataFrame, columns: str, values: str, index: list[str]) -> pd.DataFrame:
    dup = df.duplicated([DATE, columns])
    if dup.any():
        examples = df.loc[dup, [DATE, columns]].head(5).to_dict("records")
        raise ValueError(f"Duplicated ({DATE}, {columns}) rows: {examples}")
    wide = df.pivot(index=DATE, columns=columns, values=values).astype(float)
    wide = wide.reindex(pd.Index(index, name=DATE))
    wide.columns.name = columns
    return wide


def _db_range(plan: EvaluationPlan, match_on: str) -> tuple[str, str]:
    """DB から取得する日次データの範囲 ``(start, end]``（ホライズン分延長済み）。"""
    values = plan.calendar.frame[match_on]
    if plan.anchor is not None:
        start = values.iloc[plan.anchor]
    else:
        start = shift_day(values.iloc[plan.first], -1)
    return start, values.iloc[int(plan.ext_pos[-1])]


def _load_period_frames(
    source: FileSource | DBSource,
    plan: EvaluationPlan,
    ctx: LoadContext,
    columns: str,
    values: Mapping[str, str],
    required_roles: set[str],
    convention: Convention,
) -> dict[str, pd.DataFrame]:
    """延長行の期間データを読み、values の各列を wide（行=延長行）に変換する。

    DB ソースは日次で取得し、``values`` ごとの集約方法（compound / sum）で期間に集約する。
    """
    keys = plan.ext_keys
    if isinstance(source, DBSource):
        start, end = _db_range(plan, source.match_on)
        daily = load_daily(source, start, end, ctx, required_roles)
        boundaries = plan.ext_rows.set_index(plan.calendar.index)[source.match_on]
        check_increasing(boundaries, source.match_on)
        daily_index = sorted(daily[DATE].unique())
        return {
            name: to_period_returns(
                _to_wide(daily, columns, col, daily_index),
                boundaries,
                convention,
                method,
            )
            for name, (col, method) in values.items()
        }
    df = load_snapshot(source, plan.ext_rows, ctx, required_roles, plan.optional_keys)
    return {name: _to_wide(df, columns, col, keys) for name, (col, _) in values.items()}


# ---------------------------------------------------------------------------
# DataBundle の組み立て
# ---------------------------------------------------------------------------


@dataclass
class PreparedData:
    bundle: DataBundle
    plan: EvaluationPlan
    warnings: list[str] = field(default_factory=list)


def _load_signals(cfg: EvaluationConfig, plan: EvaluationPlan, ctx: LoadContext) -> pd.DataFrame:
    frames = []
    for source in cfg.data.signals:
        df = load_snapshot(source, plan.eval_rows, ctx, {ASSET_ID})
        missing = set(source.values) - set(df.columns)
        if missing:
            raise ValueError(f"Signal columns not found in {source}: {sorted(missing)}")
        values = df.set_index([DATE, ASSET_ID])[source.values]
        frames.append(values.apply(pd.to_numeric, errors="raise").astype(float))
    signals = pd.concat(frames, axis=1, join="outer") if len(frames) > 1 else frames[0]
    return signals.sort_index()


def _load_risk_model(
    name: str,
    model: RiskModelConfig,
    plan: EvaluationPlan,
    ctx: LoadContext,
    warnings: list[str],
) -> RiskModelData:
    exposures = factor_covariance = specific_risk = None
    forward_factor = {}
    if model.exposures is not None:
        df = load_snapshot(model.exposures, plan.eval_rows, ctx, {ASSET_ID})
        exposures = df.set_index([DATE, ASSET_ID]).select_dtypes("number").sort_index()
    if model.factor_covariance is not None:
        df = load_snapshot(model.factor_covariance, plan.eval_rows, ctx)
        factor_covariance = df.set_index(DATE)
    if model.specific_risk is not None:
        df = load_snapshot(model.specific_risk, plan.eval_rows, ctx, {ASSET_ID})
        specific_risk = df.set_index([DATE, ASSET_ID]).sort_index()
    if model.factor_returns is not None:
        source = model.factor_returns
        # ファクターリターンは「日付×ファクター」。日次→期間の集約は加算
        (period,) = _load_period_frames(
            source,
            plan,
            ctx,
            columns=FACTOR,
            values={"factor_returns": (VALUE, "sum")},
            required_roles={FACTOR, VALUE},
            convention=source.convention,
        ).values()
        fwd = forward_returns(period, plan.horizons, source.convention, "sum")
        forward_factor = {h: f.reindex(plan.eval_index) for h, f in fwd.items()}
    if model.specific_return is None:
        warnings.append(f"risk_models.{name}.specific_return is not set")
    return RiskModelData(
        exposures=exposures,
        factor_covariance=factor_covariance,
        specific_risk=specific_risk,
        forward_factor_returns=forward_factor,
        specific_return=model.specific_return,
        factor_groups=model.factor_groups,
    )


def _coverage_warnings(
    signals: pd.DataFrame, fwd: dict[str, dict[int, pd.DataFrame]], horizon: int
) -> list[str]:
    """シグナルとリターンの銘柄がほとんど一致しない場合に警告する（銘柄コードの書式違い等）。"""
    warnings = []
    for name, by_h in fwd.items():
        values = by_h[horizon].stack()
        matched = signals.index.isin(values.index[values.notna()]).mean() if len(signals) else 0.0
        if matched < 0.5:
            warnings.append(
                f"Only {matched:.0%} of signal rows have {name!r} forward returns (h={horizon}); "
                "check that asset_id formats match across sources"
            )
    return warnings


def build_bundle(cfg: EvaluationConfig) -> PreparedData:
    """config の data セクションから DataBundle を組み立てる。"""
    if cfg.calendar is None or cfg.data is None:
        raise ValueError("`calendar` and `data` are required to load data from config")
    calendar = load_calendar(cfg.calendar.path, cfg.calendar.index)
    plan = make_plan(calendar, cfg.period.start, cfg.period.end, cfg.horizons)
    warnings: list[str] = []
    if plan.missing_tail:
        warnings.append(
            f"Calendar ends {plan.missing_tail} row(s) short of the longest horizon; "
            "forward returns at the end of the period are missing"
        )

    cache = ParquetCache(cfg.cache.dir) if cfg.cache.enabled else None
    with LoadContext(calendar, cache) as ctx:
        signals = _load_signals(cfg, plan, ctx)

        if cfg.universe is not None:
            members = load_snapshot(cfg.universe, plan.eval_rows, ctx, {ASSET_ID})
            members = pd.MultiIndex.from_frame(members[[DATE, ASSET_ID]])
            signals = signals[signals.index.isin(members)]

        classification = None
        if cfg.data.classification is not None:
            df = load_snapshot(cfg.data.classification, plan.eval_rows, ctx, {ASSET_ID, CATEGORY})
            classification = df.set_index([DATE, ASSET_ID]).sort_index()

        fwd: dict[str, dict[int, pd.DataFrame]] = {}
        for source in cfg.returns:
            required = {ASSET_ID} | ({DATE} if isinstance(source, ReturnsDBSource) else set())
            periods = _load_period_frames(
                source,
                plan,
                ctx,
                columns=ASSET_ID,
                values={
                    name: (s.column, PERIOD_AGGREGATION[s.kind])
                    for name, s in source.series.items()
                },
                required_roles=required,
                convention=source.convention,
            )
            for name, period in periods.items():
                kind = source.series[name].kind
                by_h = forward_returns(
                    period, plan.horizons, source.convention, PERIOD_AGGREGATION[kind]
                )
                fwd[name] = {h: f.reindex(plan.eval_index) for h, f in by_h.items()}

        risk_models = {
            name: _load_risk_model(name, model, plan, ctx, warnings)
            for name, model in cfg.risk_models.items()
        }

    warnings += _coverage_warnings(signals, fwd, min(plan.horizons))
    for w in warnings:
        logger.warning(w)
    bundle = DataBundle(
        signals=signals,
        forward_returns=fwd,
        return_kinds=cfg.return_kinds,
        classification=classification,
        risk_models=risk_models,
        dates=tuple(plan.eval_keys),
    )
    return PreparedData(bundle=bundle, plan=plan, warnings=warnings)


def make_bundle(
    signals: pd.DataFrame,
    period_returns: Mapping[str, pd.DataFrame],
    *,
    return_kinds: Mapping[str, ReturnKind],
    horizons: list[int],
    convention: Convention = "realized",
    classification: pd.DataFrame | None = None,
    universe: pd.MultiIndex | pd.DataFrame | None = None,
    dates: list[str] | None = None,
) -> DataBundle:
    """手元の DataFrame から DataBundle を作る（io / loaders をバイパスする Python API 用）。

    Args:
        signals: index=(date, asset_id)、columns=シグナル名。
        period_returns: 系列名 -> 期間リターン（index=date, columns=asset_id）。
            行は欠けのない連続したカレンダー行で、評価期間の後ろに最大ホライズン分の行を含むこと。
        return_kinds: 系列名 -> ``total`` / ``residual``。
        convention: 期間リターンの規約（realized: 行 t は t-1→t の実現リターン）。
        universe: 評価対象の (date, asset_id)。指定時はシグナルをこの範囲に絞る。
        dates: 評価対象のカレンダー行。省略時はシグナルの日付。
    """
    signals = signals.copy()
    signals.index = signals.index.set_names([DATE, ASSET_ID])
    if universe is not None:
        members = universe if isinstance(universe, pd.MultiIndex) else universe.index
        signals = signals[signals.index.isin(members)]
    if dates is None:
        dates = sorted(signals.index.get_level_values(DATE).unique())
    fwd = {}
    for name, period in period_returns.items():
        missing = set(dates) - set(period.index)
        if missing:
            raise ValueError(f"period_returns[{name!r}] is missing dates: {sorted(missing)[:5]}")
        by_h = forward_returns(
            period.sort_index(),
            horizons,
            convention,
            PERIOD_AGGREGATION[return_kinds[name]],
        )
        fwd[name] = {}
        for h, f in by_h.items():
            f = f.reindex(pd.Index(dates, name=DATE))
            f.columns.name = ASSET_ID
            fwd[name][h] = f
    if classification is not None:
        classification = classification.copy()
        classification.index = classification.index.set_names([DATE, ASSET_ID])
    return DataBundle(
        signals=signals.sort_index(),
        forward_returns=fwd,
        return_kinds={k: return_kinds[k] for k in period_returns},
        classification=classification,
        dates=tuple(dates),
    )
