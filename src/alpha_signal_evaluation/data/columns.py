"""意味的な役割（role）と実際のカラム名のマッピング。

データソースごとに異なるカラム名（例: 銘柄コードが ``nri_code`` や ``code``）を、
読み込み直後に共通の role 名（``asset_id`` / ``date`` / ``weight`` など）へリネームする。
以降の処理（loaders・pipeline・metrics）は role 名だけを使い、実カラム名を意識しない。

role 名の定数（``DATE`` / ``ASSET_ID`` など）もここで定義し、パッケージ全体で共有する。
マッピングの中身は config の各データソースの ``columns``（role -> 実カラム名）から作られる。
"""

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

DATE = "date"
ASSET_ID = "asset_id"
CATEGORY = "category"
FACTOR = "factor"
VALUE = "value"
WEIGHT = "weight"


@dataclass(frozen=True)
class ColumnMap:
    """role 名と実カラム名の対応、および必須 role の宣言。

    生成時に ``required_roles`` のすべてが ``mapping`` のキーに含まれているかを検証する。
    つまり必須 role は、実カラム名が role 名と同じであっても ``mapping`` に明示する必要がある
    （config の ``columns`` に ``{asset_id: asset_id}`` のように書く）。
    ``mapping`` に書かれていない列はリネームされずにそのまま残る。

    Attributes:
        mapping: role 名 -> データソース上の実カラム名。
        required_roles: マッピングが必須の role 名の集合。省略時は空（検証しない）。

    Raises:
        ValueError: ``required_roles`` のうち ``mapping`` にないものがある場合（生成時）。
    """

    mapping: Mapping[str, str]  # role -> 実際のカラム名
    required_roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """必須 role がすべてマッピングされていることを検証する。

        Raises:
            ValueError: ``required_roles`` に ``mapping`` のキーにない role がある場合。
                メッセージには不足している role 名をソートして含める。
        """
        missing = self.required_roles - set(self.mapping)
        if missing:
            raise ValueError(f"Missing role mappings: {sorted(missing)}")

    def rename_dict(self) -> dict[str, str]:
        """``DataFrame.rename(columns=...)`` に渡せる「実カラム名 -> role 名」の辞書を返す。

        ``mapping`` の向きを反転したもの。同じ実カラム名に複数の role を割り当てた場合は、
        後に現れた role が優先される（辞書の上書き）。

        Returns:
            実カラム名をキー、role 名を値とする辞書。
        """
        return {actual: role for role, actual in self.mapping.items()}

    def actual(self, role: str) -> str:
        """role に対応する実カラム名を返す。

        マッピングがなければ role 名そのものが実カラム名であるとみなす。

        Args:
            role: role 名（例: ``"asset_id"``）。

        Returns:
            データソース上の実カラム名。
        """
        return self.mapping.get(role, role)


def normalize_columns(df: pd.DataFrame, column_map: ColumnMap) -> pd.DataFrame:
    """読み込んだ生データの列を role 名にリネームする。

    ``column_map.mapping`` に書かれた実カラム名がすべて ``df`` に存在することを確認してから、
    実カラム名 -> role 名にリネームする。マッピングにない列は名前を変えずに残す。
    行や値には一切手を加えない（型変換や日付キーの正規化は loaders 側で行う）。

    Args:
        df: データソースから読み込んだままの DataFrame（任意の index・列）。
        column_map: role 名と実カラム名の対応。

    Returns:
        列名だけを role 名に置き換えた新しい DataFrame。index・行・値は ``df`` と同じ。

    Raises:
        ValueError: マッピングされた実カラム名のうち ``df`` に存在しない列がある場合。
            メッセージには不足している列と、実際に存在する列の一覧を含める。
    """
    missing = set(column_map.mapping.values()) - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing expected columns in DataFrame: {sorted(missing)}. "
            f"Available: {list(df.columns)}"
        )
    return df.rename(columns=column_map.rename_dict())
