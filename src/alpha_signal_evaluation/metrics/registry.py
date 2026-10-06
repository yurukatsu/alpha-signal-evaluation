"""metric の登録と探索。

- ``@register_metric`` でクラスをレジストリに登録する（名前の重複はエラー）。
- ``metrics/`` 配下のモジュールを自動 import する（``_`` 始まりと ``base`` / ``registry`` は
  除外）。→ ``metrics/<name>.py`` にファイルを置き ``@register_metric`` を付けるだけで登録される。
- entry points（group ``alpha_signal_evaluation.metrics``）で外部パッケージの metric も
  読み込む。外部パッケージは ``pyproject.toml`` に例えば次のように書き、読み込まれる
  モジュール（またはオブジェクト）の import 時に ``@register_metric`` が実行されるようにする::

      [project.entry-points."alpha_signal_evaluation.metrics"]
      my_metric = "my_package.my_metric"

探索（:func:`discover_metrics`）は :func:`get_metric` / :func:`list_metrics` の初回呼び出し時に
一度だけ行われる。
"""

import importlib
import inspect
import logging
import pkgutil
from importlib.metadata import entry_points

from .base import Metric, MetricParams

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "alpha_signal_evaluation.metrics"
_INFRA_MODULES = {"base", "registry"}

_REGISTRY: dict[str, type[Metric]] = {}
_discovered = False


def register_metric[M: type[Metric]](cls: M) -> M:
    """``Metric`` のサブクラスをレジストリに登録するクラスデコレータ。

    登録時に次を検証する: ``Metric`` のサブクラスであること、``compute`` が実装済み
    （抽象クラスでない）こと、``name`` が空でない文字列であること、``Params`` が
    :class:`~alpha_signal_evaluation.metrics.base.MetricParams` のサブクラスであること。

    同じ ``name`` が別のクラス（モジュール名 + 修飾名で比較）で登録済みならエラー。
    同じクラス（モジュールの再 import 等）の再登録は許され、上書きされる。

    Args:
        cls: 登録する metric クラス。

    Returns:
        ``cls`` そのもの（デコレータとして使えるように）。

    Raises:
        TypeError: 上記の検証に失敗した場合。
        ValueError: ``name`` が別のクラスで登録済みの場合。
    """
    if not (inspect.isclass(cls) and issubclass(cls, Metric)):
        raise TypeError(f"@register_metric can only decorate Metric subclasses: {cls!r}")
    if inspect.isabstract(cls):
        raise TypeError(f"{cls.__name__} does not implement compute()")
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"{cls.__name__} must define a non-empty class attribute `name`")
    if not (inspect.isclass(cls.Params) and issubclass(cls.Params, MetricParams)):
        raise TypeError(f"{cls.__name__}.Params must be a subclass of MetricParams")

    existing = _REGISTRY.get(name)
    if existing is not None and _qualname(existing) != _qualname(cls):
        raise ValueError(
            f"Metric name {name!r} is already registered by {_qualname(existing)}; "
            f"cannot register {_qualname(cls)}"
        )
    _REGISTRY[name] = cls
    return cls


def _qualname(cls: type) -> str:
    """クラスの完全修飾名 ``"<module>.<qualname>"`` を返す（重複登録の判定用）。

    Args:
        cls: 対象のクラス。

    Returns:
        ``"alpha_signal_evaluation.metrics.ic.InformationCoefficient"`` のような文字列。
    """
    return f"{cls.__module__}.{cls.__qualname__}"


def discover_metrics() -> None:
    """同梱の metric モジュールと entry points を一度だけ読み込む。

    1. このパッケージ（``metrics/``）直下のモジュールのうち、名前が ``_`` で始まらず
       ``base`` / ``registry`` でもないものを import する（import 時に
       ``@register_metric`` が実行されて登録される）。
    2. entry points group ``alpha_signal_evaluation.metrics`` の各エントリを ``load()`` する。
       読み込みに失敗したプラグインはログに例外を記録して無視する（同梱 metric は使える）。

    2回目以降の呼び出しは何もしない。同梱モジュールの import で例外が起きた場合は
    そのまま送出される。
    """
    global _discovered
    if _discovered:
        return
    _discovered = True
    package = importlib.import_module(__package__)
    for module in pkgutil.iter_modules(package.__path__):
        if module.name.startswith("_") or module.name in _INFRA_MODULES:
            continue
        importlib.import_module(f"{__package__}.{module.name}")
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            ep.load()
        except Exception:
            logger.exception("Failed to load metric plugin %r (%s)", ep.name, ep.value)


def get_metric(name: str) -> type[Metric]:
    """名前から metric クラスを取り出す（必要なら先に探索する）。

    Args:
        name: metric の ``name``（config.yaml の ``metrics[].name``）。

    Returns:
        登録済みの metric クラス（インスタンスではない）。

    Raises:
        ValueError: その名前の metric が登録されていない場合（利用可能な名前を含む）。
    """
    discover_metrics()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown metric {name!r}. Available: {sorted(_REGISTRY)}") from None


def list_metrics() -> dict[str, type[Metric]]:
    """登録済みの全 metric を名前順で返す（必要なら先に探索する）。

    Returns:
        name → metric クラスの辞書（名前の昇順）。レジストリのコピーなので変更しても
        レジストリには影響しない。
    """
    discover_metrics()
    return dict(sorted(_REGISTRY.items()))
