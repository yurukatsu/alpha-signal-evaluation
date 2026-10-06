from dataclasses import dataclass, field

from ..config import ConnectionConfig, ServerType, _env_factory


@dataclass(kw_only=True)
class IRDDBConfig(ConnectionConfig):
    server_type: ServerType = "sqlserver"
    host: str = field(default_factory=_env_factory("IRDDB_HOST"))  # 社内ホスト名は公開しない
    port: int = 1433
