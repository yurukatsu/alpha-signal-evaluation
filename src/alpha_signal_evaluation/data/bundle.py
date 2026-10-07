"""metrics に渡す標準化済みデータ（``DataBundle``）と、その中身の記述（``DataSpec``）。

すべて ``calendar.index``（通常 Base_day）の値で整列済みであり、同じ行のシグナルと
フォワードリターンは時点合わせ済みであることを pipeline が保証する。
metrics はここにあるデータを結合・集計するだけで、時点をずらしてはいけない。

``DataBundle`` は pipeline/preprocess.py（config から読み込む場合は ``build_bundle``、
手元の DataFrame からは ``make_bundle``）が組み立てる。このモジュール自体はデータを
読み込まず、時点合わせも行わない。提供するのは、データの保持・「何が揃っているか」の
記述（``spec``）・metric 向けの省略値の解決と整形（``aligned`` / ``signal_panel``）だけである。

モジュール定数:

- ``RISK_MODEL_COMPONENTS``: リスクモデルの構成要素名（``exposures`` /
  ``factor_covariance`` / ``specific_risk`` / ``factor_returns``）。
- ``REQUIREMENTS``: ``Metric.requires`` に書ける名前の全集合。``signals`` /
  ``forward_returns`` / ``classification`` / ``risk_models`` / ``benchmark_weights`` /
  ``portfolio_weights`` と、``risk_models.<構成要素名>``。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

import pandas as pd

from ..config import ReturnKind
from .columns import ASSET_ID, DATE

RISK_MODEL_COMPONENTS = frozenset(
    {"exposures", "factor_covariance", "specific_risk", "factor_returns"}
)

# Metric.requires に書ける名前
REQUIREMENTS = frozenset(
    {
        "signals",
        "forward_returns",
        "classification",
        "risk_models",
        "benchmark_weights",
        "portfolio_weights",
        *(f"risk_models.{c}" for c in RISK_MODEL_COMPONENTS),
    }
)


@dataclass(frozen=True)
class RiskModelData:
    """1つのリスクモデルのデータ。各構成要素は省略可能（config で定義したものだけが入る）。

    行の日付キーはすべて評価対象のカレンダー行（``calendar.index`` の値）。
    ``forward_factor_returns`` は ``DataBundle.forward_returns`` と同じ規約で pipeline が
    時点合わせ済み（行 t の値は、行 t のシグナルに対応する h 期先までのファクターリターン）。

    Attributes:
        exposures: ファクターエクスポージャー。index=(date, asset_id)、columns=ファクター名。
            config から読み込む場合は数値列だけが残される。
        factor_covariance: ファクター共分散。Barra（``source: barra``）では
            index=(date, factor)、columns=ファクター名で、日付ごとの正方行列を縦に積んだもの
            （年率、小数の2乗）。それ以外のソースでは index=date、列の形式はソースのまま
            （このパッケージでは解釈しない）。
        specific_risk: スペシフィックリスク。index=(date, asset_id)、列はソースのまま。
        forward_factor_returns: ホライズン（カレンダー行数） -> フォワードファクターリターン
            （index=date、columns=ファクター名）。期間をまたぐ集約は加算。
            ファクターリターンを定義していなければ空の辞書。
        specific_return: このモデルに対応するスペシフィックリターンの系列名
            （``DataBundle.forward_returns`` のキー）。未設定なら ``None``。
        factor_groups: グループ名 -> そのグループに属するファクター名のリスト
            （例: ``{"style": [...], "industry": [...]}``）。未設定なら ``None``。
    """

    exposures: pd.DataFrame | None = None  # index=(date, asset_id), columns=ファクター
    factor_covariance: pd.DataFrame | None = None  # Barra: (date, factor) x factor
    specific_risk: pd.DataFrame | None = None  # index=(date, asset_id)
    # horizon -> (date x factor)。forward_returns と同じ規約で時点合わせ済み（加算で集約）
    forward_factor_returns: dict[int, pd.DataFrame] = field(default_factory=dict)
    specific_return: str | None = None  # 対応するスペシフィックリターンの系列名
    factor_groups: dict[str, list[str]] | None = None

    @property
    def components(self) -> frozenset[str]:
        """データが揃っている構成要素名の集合。

        ``exposures`` / ``factor_covariance`` / ``specific_risk`` は ``None`` でなければ、
        ``factor_returns`` は ``forward_factor_returns`` が空でなければ含まれる。
        ``DataSpec`` の ``risk_models.<構成要素名>`` の判定に使う。

        Returns:
            ``RISK_MODEL_COMPONENTS`` の部分集合。
        """
        present = {
            "exposures": self.exposures is not None,
            "factor_covariance": self.factor_covariance is not None,
            "specific_risk": self.specific_risk is not None,
            "factor_returns": bool(self.forward_factor_returns),
        }
        return frozenset(k for k, v in present.items() if v)

    def factors(self, group: str | None = None) -> list[str]:
        """エクスポージャーのファクター名を返す。``group`` 指定時は factor_groups で絞る。

        順序は ``exposures`` の列の順。``group`` が ``None`` の場合、``factor_groups`` が
        未設定（または空）の場合、``group`` が ``factor_groups`` にない場合は、
        絞り込まずに全ファクターを返す（エラーにはしない）。グループのメンバーのうち
        ``exposures`` の列にないものは無視する。

        Args:
            group: ``factor_groups`` のグループ名。``None`` なら全ファクター。

        Returns:
            ファクター名のリスト。``exposures`` がなければ空リスト。
        """
        if self.exposures is None:
            return []
        factors = list(self.exposures.columns)
        if group is None or not self.factor_groups or group not in self.factor_groups:
            return factors
        members = set(self.factor_groups[group])
        return [f for f in factors if f in members]


@dataclass(frozen=True)
class DataSpec:
    """データの中身を持たない「何が揃っているか」の記述。config の意味の検証に使う。

    データを読み込む前に config から作る場合（``validation.spec_from_config``）と、
    組み立て済みの bundle から作る場合（``DataBundle.spec``）がある。
    ``Metric.check`` はこれを使って、``requires`` のデータが揃っているか、params の
    signals / returns / horizons / risk_model が実在するかを計算前に検証する。

    Attributes:
        provides: 利用可能なデータ名の集合（``REQUIREMENTS`` の部分集合）。
        signals: シグナル名。
        return_kinds: リターン系列名 -> 種類（``total`` / ``specific``）。
        horizons: 利用可能なホライズン（カレンダー行数）。
        risk_models: リスクモデル名 -> 揃っている構成要素名の集合。
    """

    provides: frozenset[str]
    signals: tuple[str, ...] = ()
    return_kinds: Mapping[str, ReturnKind] = field(default_factory=dict)
    horizons: tuple[int, ...] = ()
    risk_models: Mapping[str, frozenset[str]] = field(default_factory=dict)


def _empty_signals() -> pd.DataFrame:
    """列を持たない空のシグナル DataFrame（index=(date, asset_id) の空の MultiIndex）を返す。

    ``DataBundle.restrict`` で signals を要求しない metric に渡すためのもの。
    """
    index = pd.MultiIndex.from_arrays([[], []], names=[DATE, ASSET_ID])
    return pd.DataFrame(index=index)


@dataclass(frozen=True)
class DataBundle:
    """metrics に渡す、時点合わせ済みの評価データ一式。

    同じ ``date`` の行にあるシグナルとフォワードリターンは pipeline が整列済みであり、
    metrics はこれを結合・集計するだけで時点をずらしてはいけない。``date`` の値はすべて
    カレンダーの ``index`` 列（通常 Base_day）の文字列キー。

    生成時に、signals の index 名が ``(date, asset_id)`` であること、forward_returns の
    全系列に return_kinds が定義されていることを検証する。``dates`` を省略した場合は
    signals に現れる日付（重複除去・昇順）で補う。

    Attributes:
        signals: シグナル値。index=(date, asset_id)、columns=シグナル名。
        forward_returns: 系列名 -> ホライズン（カレンダー行数） -> フォワードリターン
            （index=date、columns=asset_id の wide 形式）。行 t の値は、行 t のシグナルに
            対応する h 期先までの累積リターン（pipeline で時点合わせ済み）。
            期間をまたぐ集約は ``total`` が複利、``specific`` が加算。
        return_kinds: 系列名 -> リターンの種類（``total`` / ``specific``）。
            forward_returns のすべての系列について必要。
        classification: 銘柄の分類。index=(date, asset_id)、columns=[category, ...]。
            未設定なら ``None``。
        risk_models: リスクモデル名 -> ``RiskModelData``。
        benchmark_weights: ベンチマークウェイト。index=(date, asset_id)、columns=[weight]。
            未設定なら ``None``。
        portfolio_weights: 将来の拡張用（名前 -> ウェイト）。現状 pipeline は設定しない。
        dates: 評価対象のカレンダー行の日付キー（昇順）。欠けのない連続した行であることを
            pipeline が保証する。シグナルが全く無い行も含む。

    Raises:
        ValueError: signals の index 名が ``(date, asset_id)`` でない場合、または
            forward_returns に return_kinds のない系列がある場合（生成時）。
    """

    signals: pd.DataFrame  # index=(date, asset_id), columns=シグナル名
    forward_returns: dict[str, dict[int, pd.DataFrame]] = field(default_factory=dict)
    return_kinds: dict[str, ReturnKind] = field(default_factory=dict)
    classification: pd.DataFrame | None = None  # index=(date, asset_id), columns=[category, ...]
    risk_models: dict[str, RiskModelData] = field(default_factory=dict)
    benchmark_weights: pd.DataFrame | None = None  # index=(date, asset_id), columns=[weight]
    portfolio_weights: dict[str, pd.DataFrame] = field(default_factory=dict)  # 将来
    # 評価対象のカレンダー行（昇順）。欠けのない連続した行であることを pipeline が保証する
    dates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """index 名と return_kinds を検証し、``dates`` が空なら signals の日付で補う。

        Raises:
            ValueError: signals の index 名が ``(date, asset_id)`` でない場合、または
                forward_returns のキーのうち return_kinds にないものがある場合。
        """
        if list(self.signals.index.names) != [DATE, ASSET_ID]:
            raise ValueError(
                f"signals index must be ({DATE}, {ASSET_ID}), got {self.signals.index.names}"
            )
        missing = set(self.forward_returns) - set(self.return_kinds)
        if missing:
            raise ValueError(f"return_kinds is missing for series: {sorted(missing)}")
        if not self.dates:
            dates = self.signals.index.get_level_values(DATE).unique().sort_values()
            object.__setattr__(self, "dates", tuple(dates))

    # --- 何が揃っているか -----------------------------------------------------

    @property
    def signal_names(self) -> list[str]:
        """シグナル名のリスト（signals の列順）。"""
        return list(self.signals.columns)

    @property
    def return_names(self) -> list[str]:
        """フォワードリターンの系列名のリスト（forward_returns の挿入順）。"""
        return list(self.forward_returns)

    @property
    def horizons(self) -> list[int]:
        """全系列に現れるホライズンの和集合を昇順に並べたリスト。リターンがなければ空。"""
        return sorted({h for by_h in self.forward_returns.values() for h in by_h})

    def spec(self) -> DataSpec:
        """この bundle に揃っているデータを ``DataSpec`` として記述する。

        ``provides`` には、シグナル列が1つ以上あれば ``signals``、各データが ``None`` /
        空でなければその名前を入れる。リスクモデルがあれば ``risk_models`` に加えて、
        いずれかのモデルが持つ構成要素ごとに ``risk_models.<構成要素名>`` を入れる。

        Returns:
            データの中身を持たない ``DataSpec``。
        """
        provides: set[str] = set()
        if len(self.signals.columns):
            provides.add("signals")
        if self.forward_returns:
            provides.add("forward_returns")
        if self.classification is not None:
            provides.add("classification")
        if self.benchmark_weights is not None:
            provides.add("benchmark_weights")
        if self.portfolio_weights:
            provides.add("portfolio_weights")
        if self.risk_models:
            provides.add("risk_models")
            for model in self.risk_models.values():
                provides |= {f"risk_models.{c}" for c in model.components}
        return DataSpec(
            provides=frozenset(provides),
            signals=tuple(self.signal_names),
            return_kinds=dict(self.return_kinds),
            horizons=tuple(self.horizons),
            risk_models={n: m.components for n, m in self.risk_models.items()},
        )

    def restrict(self, requires: Iterable[str]) -> "DataBundle":
        """``requires`` に含まれないデータを空にしたコピーを返す（契約テスト用）。

        metric が ``requires`` で宣言したデータだけで計算できることを確かめるために使う。
        要求されないデータは、signals なら列のない空の DataFrame、forward_returns・
        return_kinds・portfolio_weights なら空の辞書、classification・benchmark_weights なら
        ``None`` に置き換える。return_kinds は ``forward_returns`` を要求したときだけ残す。
        ``dates`` は常に元の値を引き継ぐ。

        リスクモデルは、``risk_models`` を要求すればそのまま残し、``risk_models.<構成要素名>``
        だけを要求した場合は各モデルの要求されない構成要素を ``None``
        （``factor_returns`` なら ``forward_factor_returns`` を空の辞書）にしたコピーにする
        （``specific_return`` と ``factor_groups`` は残す）。どちらも要求しなければ空の辞書。

        Args:
            requires: 残すデータ名（``REQUIREMENTS`` の名前。未知の名前は単に無視される）。

        Returns:
            新しい ``DataBundle``。データ自体はコピーせず、元のオブジェクトを共有する。
        """
        requires = set(requires)
        risk_components = {r.split(".", 1)[1] for r in requires if r.startswith("risk_models.")}
        risk_models: dict[str, RiskModelData] = {}
        if "risk_models" in requires:
            risk_models = self.risk_models
        elif risk_components:
            drop = RISK_MODEL_COMPONENTS - risk_components
            risk_models = {
                name: replace(
                    model,
                    **{c: None for c in drop if c != "factor_returns"},
                    **({"forward_factor_returns": {}} if "factor_returns" in drop else {}),
                )
                for name, model in self.risk_models.items()
            }
        has_returns = "forward_returns" in requires
        return DataBundle(
            signals=self.signals if "signals" in requires else _empty_signals(),
            forward_returns=self.forward_returns if has_returns else {},
            return_kinds=self.return_kinds if has_returns else {},
            classification=self.classification if "classification" in requires else None,
            risk_models=risk_models,
            benchmark_weights=(self.benchmark_weights if "benchmark_weights" in requires else None),
            portfolio_weights=(self.portfolio_weights if "portfolio_weights" in requires else {}),
            dates=self.dates,
        )

    # --- params の省略値の解決 ----------------------------------------------

    def resolve_signals(self, names: Iterable[str] | None) -> list[str]:
        """params の ``signals`` の省略値を解決する。

        Args:
            names: シグナル名。``None`` なら省略とみなす。

        Returns:
            省略時は全シグナル名（signals の列順）、指定時は ``names`` をそのまま
            リストにしたもの（存在の検証は ``Metric.check`` で行う）。
        """
        return self.signal_names if names is None else list(names)

    def resolve_returns(self, names: Iterable[str] | None) -> list[str]:
        """params の ``returns`` の省略値を解決する。

        省略時は定義済みの全系列にする（``total`` のような名前固定のデフォルトにはしない）。

        Args:
            names: リターン系列名。``None`` なら省略とみなす。

        Returns:
            省略時は全系列名、指定時は ``names`` をそのままリストにしたもの。
        """
        return self.return_names if names is None else list(names)

    def resolve_horizons(self, horizons: Iterable[int] | None) -> list[int]:
        """params の ``horizons`` の省略値を解決する。

        Args:
            horizons: ホライズン（カレンダー行数）。``None`` なら省略とみなす。

        Returns:
            省略時は bundle の全ホライズン（昇順）、指定時は ``horizons`` を昇順に並べたもの。
        """
        return self.horizons if horizons is None else sorted(horizons)

    def risk_model(self, name: str | None = None) -> tuple[str, RiskModelData]:
        """名前でリスクモデルを引く。名前を省略した場合、リスクモデルが1つだけならそれを返す。

        Args:
            name: リスクモデル名。``None`` なら、定義されているリスクモデルが
                ちょうど1つのときにそれを選ぶ。

        Returns:
            ``(リスクモデル名, RiskModelData)`` のタプル。

        Raises:
            ValueError: ``name`` が ``None`` でリスクモデルが1つでない（0個または複数）場合、
                または ``name`` のリスクモデルが存在しない場合。
        """
        if name is None:
            if len(self.risk_models) != 1:
                raise ValueError(f"Specify risk_model; available: {sorted(self.risk_models)}")
            name = next(iter(self.risk_models))
        try:
            return name, self.risk_models[name]
        except KeyError:
            raise ValueError(
                f"Unknown risk model {name!r}; available: {sorted(self.risk_models)}"
            ) from None

    # --- metrics 向けのデータ整形（時点のずらしは一切しない） ---------------------

    def aligned(self, signal: str, returns: str, horizon: int) -> pd.DataFrame:
        """同じ (date, asset_id) のシグナルとフォワードリターンを結合する。

        フォワードリターン（date x asset_id の wide）を long に変換し、シグナルと
        (date, asset_id) で内部結合する。時点合わせは pipeline で済んでいるので、
        ここでは同じキーの値を並べるだけで時点はずらさない。

        Args:
            signal: シグナル名。
            returns: リターン系列名。
            horizon: ホライズン（カレンダー行数）。

        Returns:
            index=(date, asset_id)、columns=[signal, "forward_return"] の DataFrame。
            どちらかが欠損している行は除く。

        Raises:
            KeyError: ``signal``・``returns``・``horizon`` のいずれかが bundle にない場合。
        """
        fwd = self.forward_returns[returns][horizon].stack().rename("forward_return")
        fwd.index.names = [DATE, ASSET_ID]
        return self.signals[[signal]].join(fwd, how="inner").dropna()

    def signal_panel(self, signal: str) -> pd.DataFrame:
        """シグナルを (date x asset) の wide 形式にし、評価対象の全カレンダー行で reindex する。

        ``dates`` の全行を持つので、シグナルが1件もない日付も全欠損の行として残る。
        これにより行のずれ（ラグ）を「カレンダーの行数」として数えられる。

        Args:
            signal: シグナル名。

        Returns:
            index=date（``dates`` の全行、昇順）、columns=asset_id の DataFrame。
            値のない組み合わせは欠損。

        Raises:
            KeyError: ``signal`` が signals の列にない場合。
        """
        panel = self.signals[signal].unstack(ASSET_ID)
        return panel.reindex(pd.Index(self.dates, name=DATE))
