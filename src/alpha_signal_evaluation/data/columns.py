"""意味的な役割（role）と実際のカラム名のマッピング。

読み込み時に role 名へリネームし、以降の処理は role 名だけを使う。
"""

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

DATE = "date"
ASSET_ID = "asset_id"
CATEGORY = "category"
FACTOR = "factor"
VALUE = "value"


@dataclass(frozen=True)
class ColumnMap:
    mapping: Mapping[str, str]  # role -> 実際のカラム名
    required_roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        missing = self.required_roles - set(self.mapping)
        if missing:
            raise ValueError(f"Missing role mappings: {sorted(missing)}")

    def rename_dict(self) -> dict[str, str]:
        return {actual: role for role, actual in self.mapping.items()}

    def actual(self, role: str) -> str:
        """role に対応する実カラム名。マッピングがなければ role 名そのものとみなす。"""
        return self.mapping.get(role, role)


def normalize_columns(df: pd.DataFrame, column_map: ColumnMap) -> pd.DataFrame:
    missing = set(column_map.mapping.values()) - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing expected columns in DataFrame: {sorted(missing)}. "
            f"Available: {list(df.columns)}"
        )
    return df.rename(columns=column_map.rename_dict())
