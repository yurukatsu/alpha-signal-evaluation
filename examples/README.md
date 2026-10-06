# examples

```sh
uv run python examples/make_sample_data.py          # examples/input にサンプルデータを作る
uv run alpha-eval validate examples/config.example.yaml
uv run alpha-eval run examples/config.example.yaml  # examples/output に結果を書き出す
```

`config.example.yaml` はサンプルデータ用の設定例。`config.yaml` は `.gitignore` 対象なので、
自分の設定はこれをコピーして作る。
