"""接続設定に合う DB クライアントの選択と、SQL の一括実行（namdb v0.1.2 由来）。"""

from typing import TypeAlias

import pandas as pd

from .config import ConnectionConfig
from .drivers import DBClient, MySQLClient, PostgresClient, SQLServerClient

DBClientType: TypeAlias = type[SQLServerClient] | type[MySQLClient] | type[PostgresClient]


def get_client(config: ConnectionConfig) -> DBClient:
    """接続設定の ``server_type`` に応じた DB クライアントを作って返す。

    ``sqlserver`` -> ``SQLServerClient``、``mysql`` -> ``MySQLClient``、
    ``postgres`` -> ``PostgresClient``。返すクライアントは未接続なので、``connect()`` を
    呼ぶか with 文で使う。

    Args:
        config: 接続設定。

    Returns:
        未接続の ``DBClient``。

    Raises:
        ValueError: ``server_type`` が上記以外の場合。
    """
    client_cls: DBClientType

    if config.server_type == "sqlserver":
        client_cls = SQLServerClient
    elif config.server_type == "mysql":
        client_cls = MySQLClient
    elif config.server_type == "postgres":
        client_cls = PostgresClient
    else:
        raise ValueError(f"Unsupported server_type: {config.server_type}")

    return client_cls(config)


def read_db(
    config: ConnectionConfig,
    sql: str,
    params: dict | None = None,
) -> pd.DataFrame:
    """接続設定に合うクライアントで SQL を1回実行し、結果を DataFrame で返す。

    クライアントの作成・接続・実行・切断までをまとめて行う（with 文で必ず閉じる）。
    同じ接続先に何度も問い合わせる場合は ``get_client`` でクライアントを使い回す方が良い。

    Args:
        config: 接続設定。
        sql: 実行する SQL。``params`` を渡す場合は ``:name`` 形式でパラメータを参照する。
        params: バインドパラメータ。``None`` なら SQL をそのまま実行する。

    Returns:
        クエリ結果の DataFrame（列名は DB が返したまま）。

    Raises:
        ValueError: ``server_type`` が未対応の場合。
    """
    with get_client(config) as client:
        return client.execute(sql, params=params)
