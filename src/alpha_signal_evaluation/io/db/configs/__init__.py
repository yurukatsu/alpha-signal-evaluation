"""接続先ごとの ConnectionConfig。

新しい接続先を追加するときは、このディレクトリに派生クラスを定義し、
``CONNECTIONS`` に config.yaml から参照する短いキーを1行追加する。
"""

from ..config import ConnectionConfig
from .irddb import IRDDBConfig
from .risk_models import RiskModelsConfig

CONNECTIONS: dict[str, type[ConnectionConfig]] = {
    "risk_models": RiskModelsConfig,
}


def get_connection_config(key: str) -> ConnectionConfig:
    """config.yaml の ``connection`` キーから ConnectionConfig を生成する。"""
    try:
        config_class = CONNECTIONS[key]
    except KeyError:
        raise ValueError(f"Unknown connection: {key!r}. Available: {sorted(CONNECTIONS)}") from None
    return config_class()


__all__ = [
    "CONNECTIONS",
    "IRDDBConfig",
    "RiskModelsConfig",
    "get_connection_config",
]
