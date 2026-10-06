"""DB 種別ごとのクライアント実装（namdb v0.1.2 由来）。

いずれも SQLAlchemy の Engine を介して ``pandas.read_sql`` で結果を DataFrame として返す。
"""

from .base import DBClient
from .mysql import MySQLClient
from .postgres import PostgresClient
from .sqlserver import SQLServerClient

__all__ = [
    "DBClient",
    "MySQLClient",
    "PostgresClient",
    "SQLServerClient",
]
