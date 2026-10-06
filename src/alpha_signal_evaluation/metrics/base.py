"""metric の拡張契約（基底クラス・パラメータ・結果の型）。

新しい metric の作者が書くのは「``Metric`` を継承し、``name`` / ``description`` /
``requires`` / ``Params`` / ``compute`` を定義して ``@register_metric`` を付ける」だけ
（``_template.py`` をコピーして始める）。

契約の要点:
    - ``name``: config.yaml の ``metrics`` から参照する一意な名前（空文字不可）。
    - ``description``: ``metrics list`` コマンド等に表示する1行の説明（契約テストで必須）。
    - ``requires``: 使用するデータの宣言。``data.bundle.REQUIREMENTS`` に含まれる名前だけを
      書ける（クラス定義時に検証）。宣言外のデータは契約テストでは空にして渡される。
    - ``Params``: :class:`MetricParams` のサブクラス。全フィールドにデフォルト値を持たせ、
      未知のキーは拒否される。
    - ``compute``: :class:`~alpha_signal_evaluation.data.bundle.DataBundle` を受け取り
      :class:`MetricResult` を返す。データ取得・ファイル出力・時点のずらしはしない。
    - ``check``: 計算前の意味の検証（必要なら上書きして独自の検証を足す）。

``Metric`` のサブクラスは ``tests/metrics/test_contract.py`` の契約テストで自動的に
検証される（``name`` の一致、``description`` の有無、``Params()`` がデフォルトで作れること、
未知パラメータの拒否、``requires`` に制限したデータでの ``compute`` 成功、
``check`` が利用可能なデータに対してエラーを返さないこと）。
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, TypeAlias

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ReturnKind
from ..data.bundle import REQUIREMENTS, DataBundle, DataSpec

CumulativeMethod: TypeAlias = Literal["compound", "sum"]

# 期間をまたぐ累積のデフォルト。params で変更できる
CUMULATIVE_DEFAULT: dict[ReturnKind, CumulativeMethod] = {
    "total": "compound",
    "specific": "sum",
}


class MetricParams(BaseModel):
    """全 metric のパラメータの基底クラス。

    pydantic モデルで、config.yaml の ``metrics[].params`` はここで検証される。
    ``extra="forbid"`` により未知のキーはエラーにする（タイプミス等の設定ミスを計算前に
    検出するため）。``frozen=True`` のため生成後は変更できない。

    サブクラスのフィールドはすべてデフォルト値を持つこと（config で ``params`` を省略しても
    ``Params()`` で作れることが契約テストで要求される）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class SignalReturnParams(MetricParams):
    """シグナル × リターン系列 × ホライズンを対象とする metric の共通パラメータ。

    いずれも省略時（``None``）は bundle にある全て（名前固定のデフォルトにはしない）。
    ここに定義したフィールドは :meth:`Metric.check` が config に存在するかを自動で検証する。

    Attributes:
        signals: 評価するシグナル名のリスト。デフォルト ``None``（bundle の全シグナル）。
        returns: 評価するリターン系列名のリスト（例: ``["total", "specific"]``）。
            デフォルト ``None``（定義済みの全系列）。
        horizons: 評価するホライズン（カレンダーの行数）のリスト。デフォルト ``None``
            （config の全ホライズン）。指定順に関わらず昇順で処理される。
    """

    signals: list[str] | None = None
    returns: list[str] | None = None
    horizons: list[int] | None = None


class ReturnAggregation(BaseModel):
    """期間をまたぐリターン集計の規約（quantile 等で共通に使う）。

    - サマリー統計は算術平均と幾何平均の両方を出す（選択させない）。
      → :meth:`summarize`
    - 累積リターン系列は kind（``total`` / ``specific``）ごとに ``compound``（複利）/
      ``sum``（単純加算）を選べる。指定しなかった kind はデフォルト
      （total → compound、specific → sum）を維持する。 → :meth:`cumulate`

    config では metric の params の下に ``aggregation: {cumulative: {specific: compound}}``
    のように書く。未知のキーはエラー、生成後は変更不可。

    Attributes:
        cumulative: kind → 累積方法（``"compound"`` または ``"sum"``）の辞書。
            デフォルトは :data:`CUMULATIVE_DEFAULT`（``{"total": "compound",
            "specific": "sum"}``）。一部の kind だけを渡すと残りはデフォルトで補われる。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cumulative: dict[ReturnKind, CumulativeMethod] = Field(
        default_factory=lambda: dict(CUMULATIVE_DEFAULT)
    )

    @field_validator("cumulative", mode="before")
    @classmethod
    def _merge_defaults(cls, v: Any) -> Any:
        """部分的に指定された ``cumulative`` を :data:`CUMULATIVE_DEFAULT` で補完する。

        Args:
            v: 入力値。Mapping ならデフォルトの上に上書きマージする。それ以外は
                そのまま返し、pydantic の型検証に任せる。

        Returns:
            デフォルトとマージした辞書、または入力値そのもの。
        """
        if isinstance(v, Mapping):
            return {**CUMULATIVE_DEFAULT, **v}
        return v

    def cumulate(self, returns: pd.Series | pd.DataFrame, kind: ReturnKind):
        """1期間リターンの系列から累積リターン系列を作る。

        ``cumulative[kind]`` が ``"compound"`` なら ``(1 + r).cumprod() - 1``、``"sum"`` なら
        ``r.cumsum()``。欠損の期間は欠損のまま残し、累積は前後の期間をつないで続ける
        （pandas の skipna 挙動）。重複のある h > 1 のリターンに使うのは不適切。

        Args:
            returns: 1期間リターン。Series（index=date）または DataFrame
                （index=date、columns=ポートフォリオ等）。列ごとに独立して累積する。
            kind: リターンの種類（``"total"`` / ``"specific"``）。累積方法の選択に使う。

        Returns:
            入力と同じ形（index・columns）の累積リターン。
        """
        if self.cumulative[kind] == "compound":
            return (1 + returns).cumprod() - 1
        return returns.cumsum()

    @staticmethod
    def summarize(returns: pd.Series) -> dict[str, float]:
        """リターン系列の算術平均と幾何平均を求める。

        算術平均は予測力・t 値の評価用、幾何平均は実運用での成長率の目安。
        欠損は除いて計算する。幾何平均は ``expm1(mean(log1p(r)))`` で、−100% 以下の
        リターン（``r <= -1``）が1つでもあれば NaN。有効な値がなければ両方 NaN。

        Args:
            returns: リターンの時系列（index=date）。

        Returns:
            ``{"arith_mean": float, "geo_mean": float}``。
        """
        r = returns.dropna()
        if r.empty:
            return {"arith_mean": np.nan, "geo_mean": np.nan}
        geo = np.expm1(np.log1p(r).mean()) if (r > -1).all() else np.nan
        return {"arith_mean": float(r.mean()), "geo_mean": float(geo)}


@dataclass
class MetricResult:
    """metric の計算結果（レポート出力の入力になる）。

    契約テストでは ``tables`` か ``summary`` の少なくとも一方が空でないこと、
    ``tables`` の値が DataFrame、``summary`` の値が float であることが要求される。

    Attributes:
        tables: 表の名前 → DataFrame。時系列 IC、分位別リターン等の詳細な結果。
            表の名前と列は metric ごとに決まる（各 metric のクラス docstring を参照）。
        summary: キー → float の1つの数値で表せる要約（平均 IC、t 値等）。
            キーは慣例として ``"{signal}/{returns}/h{horizon}/{stat}"`` のように
            ``/`` 区切りにする。
        notes: レポートに載せる注記（計算方法の補足等）の文字列リスト。
    """

    tables: dict[str, pd.DataFrame] = field(default_factory=dict)  # 時系列IC、分位別リターン等
    summary: dict[str, float] = field(default_factory=dict)  # 平均IC、t 値等
    notes: list[str] = field(default_factory=list)  # レポートに載せる注記


class Metric(ABC):
    """全 metric の抽象基底クラス。

    サブクラスは次のクラス属性と :meth:`compute` を定義し、``@register_metric`` で
    登録する（``metrics/`` 直下の ``_`` で始まらないモジュールに置けば自動で import される）。

    インスタンスは検証済みのパラメータ ``self.params``（``Params`` のインスタンス）を持つ。
    pipeline は並列実行時にインスタンスを pickle してワーカーに渡すため、
    インスタンスにはパラメータ以外の大きな状態を持たせない。

    Attributes:
        name: config.yaml から参照する一意な名前。必須（空文字不可）。
        description: ``metrics list`` 等に表示する1行の説明。契約テストで必須。
        requires: 使用するデータの名前の集合。``data.bundle.REQUIREMENTS`` に含まれる名前
            （``signals`` / ``forward_returns`` / ``classification`` / ``risk_models`` /
            ``risk_models.<component>`` / ``benchmark_weights`` / ``portfolio_weights``）
            だけを書ける。未知の名前はクラス定義時に ``TypeError``。
            config で用意されていないデータを要求すると :meth:`check` がエラーを返す。
        Params: パラメータの pydantic モデル（:class:`MetricParams` のサブクラス）。
            デフォルトは空の :class:`MetricParams`。
        params: インスタンスのパラメータ（``Params`` のインスタンス）。
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires: ClassVar[frozenset[str]] = frozenset()  # 必要データを宣言（data.bundle.REQUIREMENTS）
    Params: ClassVar[type[MetricParams]] = MetricParams

    def __init__(self, params: MetricParams | Mapping[str, Any] | None = None, **kwargs: Any):
        """パラメータを検証して metric を作る。

        ``Metric(Params(...))``、``Metric({"k": v})``、``Metric(k=v)`` のいずれでも作れる。
        Mapping とキーワード引数を両方渡した場合はマージされる（キーワード引数が優先）。

        Args:
            params: ``Params`` のインスタンス、パラメータの辞書、または ``None``
                （キーワード引数のみ、またはすべてデフォルト）。
            **kwargs: 個々のパラメータ。``params`` が ``Params`` インスタンスのときは渡せない。

        Raises:
            TypeError: ``Params`` インスタンスとキーワード引数を両方渡した場合、または
                ``params`` がこの metric の ``Params`` 型でない場合。
            pydantic.ValidationError: パラメータの値が不正、または未知のキーがある場合
                （``ValueError`` のサブクラス）。
        """
        if params is None:
            params = self.Params(**kwargs)
        elif isinstance(params, Mapping):
            params = self.Params.model_validate({**params, **kwargs})
        elif kwargs:
            raise TypeError("Pass either a Params instance or keyword arguments, not both")
        if not isinstance(params, self.Params):
            raise TypeError(
                f"{type(self).__name__} expects {self.Params.__name__}, got {type(params).__name__}"
            )
        self.params = params

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """サブクラス定義時に ``requires`` の名前を検証する。

        ``requires`` のタイプミス（例: ``"signal"``）を import 時点で検出するため。

        Raises:
            TypeError: ``requires`` に ``data.bundle.REQUIREMENTS`` にない名前がある場合。
        """
        super().__init_subclass__(**kwargs)
        unknown = set(cls.requires) - REQUIREMENTS
        if unknown:
            raise TypeError(
                f"{cls.__name__}.requires has unknown names {sorted(unknown)}. "
                f"Available: {sorted(REQUIREMENTS)}"
            )

    @abstractmethod
    def compute(self, data: DataBundle) -> MetricResult:
        """metric を計算する（サブクラスで必ず実装する）。

        守るべきルール:
            - 時点をずらさない。シグナルとフォワードリターンの組は
              ``data.aligned(signal, returns, horizon)`` で取り出す（同じ行は pipeline で
              時点合わせ済み）。
            - 欠損を 0 で埋めない。
            - ``requires`` に宣言したデータ以外を使わない（契約テストでは宣言外のデータは
              空にして渡される）。
            - データ取得やファイル出力はしない（結果は :class:`MetricResult` で返すだけ）。
            - 省略可能なパラメータ（signals / returns / horizons）は
              ``data.resolve_signals`` 等で解決する。

        Args:
            data: pipeline が標準化・時点合わせ済みのデータ。

        Returns:
            表・数値サマリー・注記をまとめた :class:`MetricResult`。

        Raises:
            Exception: 計算中の例外は pipeline が metric 単位で捕捉して記録し、
                他の metric の実行は続ける。
        """
        ...

    @classmethod
    def check(cls, params: MetricParams, spec: DataSpec) -> list[str]:
        """データを読み込む前にパラメータの意味を検証し、問題をメッセージのリストで返す。

        pydantic による型・値域の検証（``Params``）の後、config から作った
        :class:`~alpha_signal_evaluation.data.bundle.DataSpec`（データの中身を持たない
        「何が揃っているか」の記述）と突き合わせる。基底クラスの実装は次を検証する:

            - ``requires`` のデータが config で用意されているか
            - ``params.signals`` が config のシグナルに存在するか
            - ``params.returns`` が定義済みのリターン系列に存在するか
            - ``params.horizons`` が config のホライズンに含まれるか
            - ``params.risk_model`` が定義済みのリスクモデルに存在するか

        パラメータは ``getattr`` で取り出すため、これらの名前のフィールドを持つ metric は
        ``SignalReturnParams`` を継承していなくても自動で検証される（値が ``None`` なら
        省略扱いで検証しない）。独自の検証を足す場合は上書きし、``super().check()`` の
        結果に追加して返す。

        Args:
            params: 検証済みの ``Params`` インスタンス。
            spec: config から作ったデータの記述。

        Returns:
            エラーメッセージのリスト。問題がなければ空リスト。
        """
        errors = []
        missing = cls.requires - spec.provides
        if missing:
            errors.append(f"requires data that is not configured: {sorted(missing)}")
        signals = getattr(params, "signals", None)
        if signals is not None and (unknown := set(signals) - set(spec.signals)):
            errors.append(f"unknown signals {sorted(unknown)}; available: {list(spec.signals)}")
        returns = getattr(params, "returns", None)
        if returns is not None and (unknown := set(returns) - set(spec.return_kinds)):
            errors.append(
                f"unknown return series {sorted(unknown)}; available: {list(spec.return_kinds)}"
            )
        horizons = getattr(params, "horizons", None)
        if horizons is not None and (unknown := set(horizons) - set(spec.horizons)):
            errors.append(
                f"horizons {sorted(unknown)} are not in the configured horizons "
                f"{list(spec.horizons)}"
            )
        risk_model = getattr(params, "risk_model", None)
        if risk_model is not None and risk_model not in spec.risk_models:
            errors.append(
                f"unknown risk_model {risk_model!r}; available: {sorted(spec.risk_models)}"
            )
        return errors
