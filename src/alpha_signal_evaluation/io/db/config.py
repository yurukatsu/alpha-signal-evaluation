import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

ServerType: TypeAlias = Literal["sqlserver", "postgres", "mysql"]


def _env_factory(var_name: str) -> Callable[[], str]:
    def factory() -> str:
        try:
            return os.environ[var_name]
        except KeyError:  # 未設定なら接続前に早期に落とす
            raise KeyError(f"Environment variable {var_name!r} is not set") from None

    return factory


@dataclass(kw_only=True)
class ConnectionConfig:
    server_type: ServerType
    host: str
    port: int
    db: str
    username: str
    password: str = field(repr=False)
    charset: str = "utf8"
