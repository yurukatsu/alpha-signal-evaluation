"""社内 DB への接続（namdb v0.1.2 を取り込んだもの）。

io 層の一部で、接続設定・クライアント生成・SQL の実行だけを担い、シグナルやリターンなどの
ドメイン知識は持たない。構成は namdb のまま:

- ``config``: 接続先ごとの ``ConnectionConfig`` サブクラス（サーバー種別・ポート・DB 名・
  文字コード）。公開リポジトリのため、ホスト名と認証情報は環境変数（``*_HOST`` /
  ``*_USERNAME`` / ``*_PASSWORD``）から読む
- ``drivers``: SQL Server / MySQL / PostgreSQL 用の SQLAlchemy ベースのクライアント
- ``client``: 設定に合うクライアントの選択（``get_client``）と一括実行（``read_db``）
- ``connections``: config.yaml の ``connection`` キーから接続設定を引く対応表
  （このリポジトリで追加したもの）

Examples:
    >>> from alpha_signal_evaluation.io.db import RiskModelsConfig, read_db
    >>> df = read_db(
    ...     RiskModelsConfig(), "SELECT ... WHERE dt > :start", params={"start": "20260101"}
    ... )  # doctest: +SKIP
"""

from .client import get_client, read_db
from .config import (
    AIModelConfig,
    CommonConfig,
    ConnectionConfig,
    DSSConfig,
    DSSDATA1Config,
    DSSDataConfig,
    EquityConfig,
    FactorGLBConfig,
    FactorJPConfig,
    FFDLConfig,
    FsTrfacGlbConfig,
    GlobalConfig,
    GXConfig,
    IDSQEConfig,
    IRDDBConfig,
    JETFVConfig,
    JPMarketConfig,
    KGXConfig,
    LB903Config,
    NMT202Config,
    RdsApp002Config,
    RiskModelsConfig,
    ServerType,
    TGXConfig,
    TGXLConfig,
    TRCConfig,
    TRSConfig,
    TVLConfig,
    XebralConfig,
)
from .connections import (
    CONNECTIONS,
    connection_names,
    get_connection_class,
    get_connection_config,
    missing_settings,
)
from .drivers import DBClient, MySQLClient, PostgresClient, SQLServerClient

__all__ = [
    "AIModelConfig",
    "CONNECTIONS",
    "CommonConfig",
    "ConnectionConfig",
    "DBClient",
    "DSSConfig",
    "DSSDATA1Config",
    "DSSDataConfig",
    "EquityConfig",
    "FactorGLBConfig",
    "FactorJPConfig",
    "FFDLConfig",
    "FsTrfacGlbConfig",
    "GlobalConfig",
    "GXConfig",
    "IDSQEConfig",
    "IRDDBConfig",
    "JETFVConfig",
    "JPMarketConfig",
    "KGXConfig",
    "LB903Config",
    "MySQLClient",
    "NMT202Config",
    "PostgresClient",
    "RdsApp002Config",
    "RiskModelsConfig",
    "SQLServerClient",
    "ServerType",
    "TGXConfig",
    "TGXLConfig",
    "TRCConfig",
    "TRSConfig",
    "TVLConfig",
    "XebralConfig",
    "connection_names",
    "get_client",
    "get_connection_class",
    "get_connection_config",
    "missing_settings",
    "read_db",
]
