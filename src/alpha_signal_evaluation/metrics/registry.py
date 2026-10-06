"""metric の登録と探索。

- ``@register_metric`` でレジストリに登録する（名前の重複はエラー）
- ``metrics/`` 配下のモジュールを自動 import する（``_`` 始まりは除外）
  → ファイルを置くだけで登録される
- entry points（group ``alpha_signal_evaluation.metrics``）で外部パッケージの metric も読み込む
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
    return f"{cls.__module__}.{cls.__qualname__}"


def discover_metrics() -> None:
    """同梱の metric モジュールと entry points を一度だけ読み込む。"""
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
    discover_metrics()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown metric {name!r}. Available: {sorted(_REGISTRY)}") from None


def list_metrics() -> dict[str, type[Metric]]:
    discover_metrics()
    return dict(sorted(_REGISTRY.items()))
