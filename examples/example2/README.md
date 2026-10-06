# example2

月次データ（ファイル名が `YYYYMM`）・BARRA 形式の ID（`INDAAA1` 等）での評価例。値はすべて乱数の合成データ。

```sh
uv run python examples/example2/make_sample_data.py          # examples/example2/input に合成データを作る
uv run alpha-eval validate examples/example2/config.example.yaml
uv run alpha-eval run examples/example2/config.example.yaml  # examples/example2/output に結果を書き出す
```

| パス | 内容 | 形式 |
|---|---|---|
| `input/calendar.dat` | カレンダー | 本番と同じ形式。先頭のコメント行 + `#Base_month Monthend Trading_month Rskmdl_month Label1 Label2`（タブ区切り、将来の月まで） |
| `input/alpha/{Base_month}.dat` | シグナル | ヘッダー `#bid comp_ew`、スペース区切り |
| `input/univ/{Base_month}.dat` | ユニバース | `# format = weight` 等のコメント行 + ID・ウェイトのスペース区切り |
| `input/bm/{Base_month}.dat` | ベンチマークウェイト | univ と同じ形式 |
| `input/returns/{Base_month}.csv` | リターン | サンプル専用（その月の realized 月次リターン） |

## 実データで試す

`input/` に実データの `alpha/`・`bm/`・`univ/`・`calendar.dat` を置き、`--keep-data` を付けて実行すると、
実データには触れずに合成リターンだけを作る（`calendar.dat` がない場合のみ月だけのカレンダーを作る）。

```sh
uv run python examples/example2/make_sample_data.py --keep-data
uv run alpha-eval run examples/example2/config.example.yaml
```

このときのリターンはシグナルと無関係な乱数なので、IC 等は 0 付近になる（パイプラインの動作確認用）。
`input/` と `output/` は `.gitignore` 対象。
