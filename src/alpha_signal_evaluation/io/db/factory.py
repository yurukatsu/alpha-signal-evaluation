from .base import DBClient
from .config import ConnectionConfig, ServerType
from .mysql import MySQLClient
from .postgres import PostgresClient
from .sqlserver import SQLServerClient

_CLIENT_CLASSES: dict[ServerType, type[DBClient]] = {
    "sqlserver": SQLServerClient,
    "postgres": PostgresClient,
    "mysql": MySQLClient,
}


def create_db_client(config: ConnectionConfig) -> DBClient:
    try:
        client_class = _CLIENT_CLASSES[config.server_type]
    except KeyError:  # YAML 等、型チェッカーの目が届かない経路への実行時防御
        raise ValueError(f"Unknown server_type: {config.server_type!r}") from None
    return client_class(config)
