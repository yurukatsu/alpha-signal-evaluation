# example1

社内の入力データ（`input.zip`）と同じ形式の合成データで評価を試す。値はすべて乱数。
ディレクトリ名はわかりやすく変えている（括弧内は input.zip での名前）。

```sh
uv run python examples/example1/make_sample_data.py          # examples/example1/input にサンプルデータを作る
uv run alpha-eval validate examples/example1/config.yaml
uv run alpha-eval run examples/example1/config.yaml  # examples/example1/output に結果を書き出す
```

`input/` は Git で管理していない（`.gitignore` 対象）ので、最初に `make_sample_data.py` で作る
（作り直すときは `input/` を消してから実行する）。

| パス | 内容 | 形式 |
|---|---|---|
| `input/calendar.dat`（`date_eom.dat`） | カレンダー | ヘッダー先頭 `#`、タブ区切り（末尾はスペース区切り） |
| `input/alpha/ai/{Label2}.csv`（`AI_v1234`） | シグナル | ヘッダー `#nri_code,score` |
| `input/alpha/dss/{Label2}.csv`（`DSS`） | シグナル | ヘッダーなし（コード, スコア） |
| `input/labels/{Label2}.csv`（`add_cs`） | 業種分類 | 縦持ち。ヘッダー `#nri_code,label`、値は `L001`〜`L010` |
| `input/univ/{Trading_day}.dat` | ユニバース | `# ...` のコメント行 + 1行1銘柄のコード |
| `input/bm/{Trading_day}.dat` | ベンチマークウェイト | `# ...` のコメント行 + タブ区切り（コード, ウェイト）。ユニバースのうち時価総額上位 300 銘柄 |
| `input/returns/{Base_day}.csv` | リターン | サンプル専用。本番は DB から日次で取得する（config のコメント参照） |

実データで試す場合は、config のパスを input.zip の名前に合わせ、リターンを DB ソースに切り替える。
サンプルの `config.yaml` は Git で管理している。自分の設定は別の場所（リポジトリ直下など）に `config.yaml` を作るか、
このファイルをコピーして書き換える（`examples/` の外の `config.yaml` は `.gitignore` 対象なのでコミットされない）。
