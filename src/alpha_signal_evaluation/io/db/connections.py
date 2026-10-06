"""config.yaml の ``connection`` キーから ConnectionConfig を引くための対応表。

キーは接続先 DB 名の小文字（例: ``risk_models``, ``factor_jp``）またはクラス名
（例: ``RiskModelsConfig``）。config.py に Config を追加すれば自動で使えるようになる。

namdb 本体にはない、このリポジトリで追加したモジュール。``CONNECTIONS`` はインポート時に
config.py のクラスを走査して作る（DB 名の小文字 -> Config クラス、キーの昇順）。
接続設定のインスタンス化（環境変数の読み込み）は ``get_connection_config`` の呼び出し時に行う。
"""

from dataclasses import MISSING, fields

from . import config as _config
from .config import ConnectionConfig


def _leaf_configs() -> dict[str, type[ConnectionConfig]]:
    """db のデフォルト値を持つ（= 接続先が決まっている）Config を DB 名で引ける辞書にする。

    config モジュールにある ``ConnectionConfig`` のサブクラス（自身を含む）のうち、
    ``db`` フィールドにデフォルト値があるものだけを対象にする（``GXConfig`` のような
    サーバー単位の親クラスは除かれる）。キーは DB 名の小文字で、昇順に並べる。
    """
    found: dict[str, type[ConnectionConfig]] = {}
    for obj in vars(_config).values():
        if not (isinstance(obj, type) and issubclass(obj, ConnectionConfig)):
            continue
        db = next(f for f in fields(obj) if f.name == "db")
        if db.default is not MISSING:
            found[db.default.lower()] = obj
    return dict(sorted(found.items()))


CONNECTIONS: dict[str, type[ConnectionConfig]] = _leaf_configs()
_BY_CLASS_NAME = {cls.__name__: cls for cls in CONNECTIONS.values()}


def connection_names() -> list[str]:
    """利用可能な接続先名（DB 名の小文字）を昇順のリストで返す。"""
    return list(CONNECTIONS)


def get_connection_class(key: str) -> type[ConnectionConfig]:
    """接続先名またはクラス名から Config クラスを引く。

    まず DB 名の小文字（例: ``risk_models``）として引き、なければクラス名
    （例: ``RiskModelsConfig``）として引く。どちらも大文字・小文字を区別する。

    Args:
        key: config.yaml の ``connection`` キーの値。

    Returns:
        ``ConnectionConfig`` のサブクラス（インスタンス化はしない）。

    Raises:
        ValueError: どちらにも該当しない場合。利用可能な接続先名をメッセージに含める。
    """
    try:
        return CONNECTIONS.get(key) or _BY_CLASS_NAME[key]
    except KeyError:
        raise ValueError(f"Unknown connection: {key!r}. Available: {connection_names()}") from None


def get_connection_config(key: str) -> ConnectionConfig:
    """config.yaml の ``connection`` キーから ConnectionConfig を生成する。

    Config クラスをデフォルト引数でインスタンス化するので、ホスト名・ユーザー名・
    パスワードはこの時点の環境変数から読まれる。環境変数が未設定でもエラーにはならず、
    その項目は ``None`` になる（``missing_settings`` で確認できる）。

    Args:
        key: 接続先名（DB 名の小文字）またはクラス名。

    Returns:
        接続設定のインスタンス。

    Raises:
        ValueError: 未知の接続先の場合。
    """
    return get_connection_class(key)()


def missing_settings(config: ConnectionConfig) -> list[str]:
    """環境変数から読めなかった項目（host / username / password）を返す。

    値が ``None`` または空文字列の項目を未設定とみなす。DB に接続する前の検証
    （validation）で、どの環境変数を設定すべきかを利用者に伝えるために使う。

    Args:
        config: 接続設定。

    Returns:
        未設定の項目名のリスト（``host`` / ``username`` / ``password`` の順）。
        すべて設定済みなら空リスト。
    """
    return [name for name in ("host", "username", "password") if not getattr(config, name)]
