"""data 層: 「何を読み込むか」と、読み込んだデータの型。

- ``calendar``: カレンダーファイル（評価の時間軸の唯一の定義）の読み込み
- ``columns``: role 名（``date`` / ``asset_id`` など）と実カラム名のマッピング
- ``loaders``: config のデータソースを読み、カレンダー行に対応づけた long 形式に揃える
- ``bundle``: metrics に渡す標準化済みデータ（``DataBundle``）

物理的な I/O は io 層に任せ、依存は data -> io の一方向。フォワードリターンなどの
時点合わせはここでは行わず、pipeline/preprocess.py だけが行う。
"""
