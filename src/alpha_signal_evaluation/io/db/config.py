"""DB 接続先ごとの接続設定（namdb v0.1.2 由来）。

``ConnectionConfig`` が接続設定の共通の形で、接続先ごとのサブクラスがサーバー種別・
ポート・DB 名・文字コードのデフォルト値を持つ。多くはサーバー単位の親クラス
（``GXConfig`` / ``IRDDBConfig`` / ``DSSConfig`` / ``LB903Config`` / ``RdsApp002Config``）を
継承し、DB 名と文字コードだけを上書きする。

ホスト名・ユーザー名・パスワードはインスタンス化のたびに環境変数から読む（``.env`` で
設定する）。公開リポジトリのため、ホスト名や IP アドレスはコードに書かない。
環境変数が未設定の項目は ``None`` になるだけで、インスタンス化の時点ではエラーにならない
（事前の確認には ``connections.missing_settings`` を使う）。
"""

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

ServerType: TypeAlias = Literal["sqlserver", "mysql", "postgres"]

# namdb v0.1.2 を取り込んだもの。公開リポジトリのため、接続先のホスト名・IP アドレスは
# コードに書かず環境変数（*_HOST）から読む（認証情報と同じく .env で設定する）。


def _env_factory(
    var_names: str | Iterable[str],
) -> Callable[[], str | None]:
    """環境変数から値を読む dataclass の ``default_factory`` を作る。

    Args:
        var_names: 環境変数名、または優先順に並べた環境変数名の列。

    Returns:
        呼び出すたびに環境変数を先頭から調べ、最初に見つかった空でない値を返す関数。
        どれも未設定（または空文字列）なら ``None`` を返す。
    """
    names = (var_names,) if isinstance(var_names, str) else tuple(var_names)

    def factory() -> str | None:
        """``names`` の順に環境変数を調べ、最初の空でない値（なければ ``None``）を返す。"""
        for name in names:
            if value := os.getenv(name):
                return value
        return None

    return factory


@dataclass(kw_only=True)
class ConnectionConfig:
    """DB 接続設定の共通の形。すべてキーワード引数で指定する。

    このクラス自体はデフォルト値を持たない（``charset`` を除く）。通常は接続先ごとの
    サブクラスをデフォルト引数でインスタンス化して使う。``password`` は repr に出さない。

    Attributes:
        server_type: サーバー種別（``sqlserver`` / ``mysql`` / ``postgres``）。
            使うクライアント（``client.get_client``）を決める。
        host: ホスト名。
        port: ポート番号。
        db: データベース名。
        username: ユーザー名。
        password: パスワード。
        charset: 文字コード（SQL Server と MySQL の接続で使う）。
    """

    server_type: ServerType
    host: str
    port: int
    db: str
    username: str
    password: str = field(repr=False)
    charset: str = "utf8"


@dataclass(kw_only=True)
class TRCConfig(ConnectionConfig):
    """TRC データベース（SQL Server）の接続設定。

    ポート 1433、文字コード cp932。ホスト・ユーザー名・パスワードは環境変数
    ``TRC_HOST`` / ``TRC_USERNAME`` / ``TRC_PASSWORD`` から読む。
    """

    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("TRC_HOST"))
    port: int = 1433
    db: str = "TRC"
    username: str = field(default_factory=_env_factory("TRC_USERNAME"))
    password: str = field(default_factory=_env_factory("TRC_PASSWORD"), repr=False)
    charset: str = "cp932"


@dataclass(kw_only=True)
class TRSConfig(ConnectionConfig):
    """TRS データベース（SQL Server）の接続設定。``NMT202Config`` の親クラスでもある。

    ポート 1433、文字コード cp932。ホスト・ユーザー名・パスワードは環境変数
    ``TRS_HOST`` / ``TRS_USERNAME`` / ``TRS_PASSWORD`` から読む。
    """

    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("TRS_HOST"))
    port: int = 1433
    db: str = "TRS"
    username: str = field(default_factory=_env_factory("TRS_USERNAME"))
    password: str = field(default_factory=_env_factory("TRS_PASSWORD"), repr=False)
    charset: str = "cp932"


@dataclass(kw_only=True)
class NMT202Config(TRSConfig):
    """NMT202 データベースの接続設定。DB 名以外は ``TRSConfig`` と同じ（同じサーバー）。"""

    db: str = "NMT202"


@dataclass(kw_only=True)
class GXConfig(ConnectionConfig):
    """GX サーバー（SQL Server）の共通設定。IDSQE, KGX, TGX, TGXL の親クラス。

    DB 名を持たないので、このクラスは直接は使わない（``connections`` の対応表にも載らない）。
    ポート 2025。ホストは環境変数 ``GX_HOST``、ユーザー名は ``GX1000_USERNAME``
    （なければ ``GX_USERNAME``）、パスワードは ``GX1000_PASSWORD``（なければ
    ``GX_PASSWORD``）から読む。
    """

    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("GX_HOST"))
    port: int = 2025
    username: str = field(default_factory=_env_factory(["GX1000_USERNAME", "GX_USERNAME"]))
    password: str = field(
        default_factory=_env_factory(["GX1000_PASSWORD", "GX_PASSWORD"]), repr=False
    )


@dataclass(kw_only=True)
class KGXConfig(GXConfig):
    """K_GX データベースの接続設定（GX サーバー、文字コード cp932）。"""

    db: str = "K_GX"
    charset: str = "cp932"


@dataclass(kw_only=True)
class TGXConfig(GXConfig):
    """T_GX データベースの接続設定（GX サーバー、文字コード cp932）。"""

    db: str = "T_GX"
    charset: str = "cp932"


@dataclass(kw_only=True)
class TGXLConfig(GXConfig):
    """T_GXL データベースの接続設定（GX サーバー、文字コード utf8）。"""

    db: str = "T_GXL"
    charset: str = "utf8"


@dataclass(kw_only=True)
class IDSQEConfig(GXConfig):
    """IDSQE データベースの接続設定（GX サーバー、文字コード cp932）。"""

    db: str = "IDSQE"
    charset: str = "cp932"


@dataclass(kw_only=True)
class IRDDBConfig(ConnectionConfig):
    """IRDDB サーバー（SQL Server）の共通設定。GLOBAL, RISK_MODELS, JP_MARKET の親クラス。

    DB 名を持たないので、このクラスは直接は使わない（``connections`` の対応表にも載らない）。
    ポート 1433。ホストは環境変数 ``IRDDB_HOST``、ユーザー名は ``IRDDB_USERNAME``
    （なければ ``LB904_USERNAME``）、パスワードは ``IRDDB_PASSWORD``（なければ
    ``LB904_PASSWORD``）から読む。
    """

    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("IRDDB_HOST"))
    port: int = 1433
    username: str = field(default_factory=_env_factory(["IRDDB_USERNAME", "LB904_USERNAME"]))
    password: str = field(
        default_factory=_env_factory(["IRDDB_PASSWORD", "LB904_PASSWORD"]), repr=False
    )


@dataclass(kw_only=True)
class GlobalConfig(IRDDBConfig):
    """GLOBAL データベースの接続設定（IRDDB サーバー、文字コード cp932）。"""

    db: str = "GLOBAL"
    charset: str = "cp932"


@dataclass(kw_only=True)
class RiskModelsConfig(IRDDBConfig):
    """RISK_MODELS データベースの接続設定（IRDDB サーバー、文字コード utf8）。

    認証情報は IRDDB 共通のものではなく、環境変数 ``RISK_MODELS_USERNAME`` /
    ``RISK_MODELS_PASSWORD`` から読む（ホストは ``IRDDB_HOST``）。
    """

    db: str = "RISK_MODELS"
    username: str = field(default_factory=_env_factory("RISK_MODELS_USERNAME"))
    password: str = field(default_factory=_env_factory("RISK_MODELS_PASSWORD"), repr=False)
    charset: str = "utf8"


@dataclass(kw_only=True)
class JPMarketConfig(IRDDBConfig):
    """JP_MARKET データベースの接続設定（IRDDB サーバー、文字コード utf8）。"""

    db: str = "JP_MARKET"
    charset: str = "utf8"


@dataclass(kw_only=True)
class DSSConfig(ConnectionConfig):
    """DSS サーバー（SQL Server）の共通設定。DSS_DATA1, JETFV の親クラス。

    DB 名を持たないので、このクラスは直接は使わない（``connections`` の対応表にも載らない）。
    ポート 1433。ホスト・ユーザー名・パスワードは環境変数 ``DSS_HOST`` /
    ``DSS_USERNAME`` / ``DSS_PASSWORD`` から読む。
    """

    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("DSS_HOST"))
    port: int = 1433
    username: str = field(default_factory=_env_factory("DSS_USERNAME"))
    password: str = field(default_factory=_env_factory("DSS_PASSWORD"), repr=False)


@dataclass(kw_only=True)
class DSSDATA1Config(DSSConfig):
    """DSS_DATA1 データベースの接続設定（DSS サーバー、文字コード cp932）。"""

    db: str = "DSS_DATA1"
    charset: str = "cp932"


@dataclass(kw_only=True)
class JETFVConfig(DSSConfig):
    """JETFV データベースの接続設定（DSS サーバー、文字コード utf8）。"""

    db: str = "JETFV"
    charset: str = "utf8"


@dataclass(kw_only=True)
class LB903Config(ConnectionConfig):
    """LB903 サーバー（MySQL）の共通設定。factor_glb, factor_jp, equity などの親クラス。

    DB 名を持たないので、このクラスは直接は使わない（``connections`` の対応表にも載らない）。
    ポート 3306、文字コード utf8。ホストは環境変数 ``LB903_HOST``、ユーザー名は
    ``LB903_USERNAME``（なければ ``LABDB903_USERNAME``）、パスワードは ``LB903_PASSWORD``
    （なければ ``LABDB903_PASSWORD``）から読む。
    """

    server_type: ServerType = "mysql"
    host: str = field(default_factory=_env_factory("LB903_HOST"))
    port: int = 3306
    username: str = field(default_factory=_env_factory(["LB903_USERNAME", "LABDB903_USERNAME"]))
    password: str = field(
        default_factory=_env_factory(["LB903_PASSWORD", "LABDB903_PASSWORD"]), repr=False
    )
    charset: str = "utf8"


@dataclass(kw_only=True)
class FactorGLBConfig(LB903Config):
    """factor_glb データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "factor_glb"


@dataclass(kw_only=True)
class FactorJPConfig(LB903Config):
    """factor_jp データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "factor_jp"


@dataclass(kw_only=True)
class EquityConfig(LB903Config):
    """equity データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "equity"


@dataclass(kw_only=True)
class XebralConfig(LB903Config):
    """xebral データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "xebral"


@dataclass(kw_only=True)
class CommonConfig(LB903Config):
    """common データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "common"


@dataclass(kw_only=True)
class TVLConfig(LB903Config):
    """tvl データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "tvl"


@dataclass(kw_only=True)
class AIModelConfig(LB903Config):
    """ai_model データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "ai_model"


@dataclass(kw_only=True)
class DSSDataConfig(LB903Config):
    """dss_data データベースの接続設定（LB903 サーバー、MySQL）。"""

    db: str = "dss_data"


@dataclass(kw_only=True)
class RdsApp002Config(ConnectionConfig):
    """RDS_APP_002 サーバー（PostgreSQL）の共通設定。fs_trfac_glb, ffdl の親クラス。

    DB 名を持たないので、このクラスは直接は使わない（``connections`` の対応表にも載らない）。
    ポート 5432、文字コード utf8（PostgreSQL クライアントは charset を使わない）。
    ホストは環境変数 ``RDS_APP_002_HOST`` から読む。ユーザー名・パスワードはどちらも
    まず ``USER_FRIENDLY_NAME`` を見て、なければそれぞれ ``RDS_APP_002_USERNAME`` /
    ``RDS_APP_002_PASSWORD`` を読む（namdb の定義のまま）。
    """

    server_type: ServerType = "postgres"
    host: str = field(default_factory=_env_factory("RDS_APP_002_HOST"))
    port: int = 5432
    username: str = field(
        default_factory=_env_factory(["USER_FRIENDLY_NAME", "RDS_APP_002_USERNAME"])
    )
    password: str = field(
        default_factory=_env_factory(["USER_FRIENDLY_NAME", "RDS_APP_002_PASSWORD"]), repr=False
    )
    charset: str = "utf8"


@dataclass(kw_only=True)
class FsTrfacGlbConfig(RdsApp002Config):
    """fs_trfac_glb データベースの接続設定（RDS_APP_002 サーバー、PostgreSQL）。"""

    db: str = "fs_trfac_glb"


@dataclass(kw_only=True)
class FFDLConfig(RdsApp002Config):
    """ffdl データベースの接続設定（RDS_APP_002 サーバー、PostgreSQL）。"""

    db: str = "ffdl"
