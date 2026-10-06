# AGENTS.md

株式アルファシグナルの評価モジュール。最優先は **拡張性**（他の運用者が `metrics/` に1ファイル置くだけで指標を追加できること）。

## コマンド

- テスト: `uv run pytest`
- Lint / format: `uv run ruff check src tests` / `uv run ruff format src tests`
- CLI: `uv run alpha-eval {run,validate,metrics list} ...`

## 必ず守る原則

1. **時点合わせは `pipeline/preprocess.py` に閉じ込める。** フォワードリターン計算・ラグ・カレンダー整列を metrics で行わない。
   metrics はシグナルとリターンを `DataBundle.aligned()` で取り出すだけ。時点合わせを変更したら
   `tests/pipeline/test_time_alignment.py` と `tests/test_end_to_end.py::test_file_pipeline_is_time_aligned` を必ず通す。
2. **欠損は欠損のまま。** リターンを 0 埋めしない（集約は `min_count=1`、ローリングは `min_periods=h`）。
3. **設定ミスは計算前にすべて検出する。** 構造は `config.py`（pydantic、`extra="forbid"`）、
   意味（カレンダー・レジストリ・ファイル存在）は `validation.py`。新しい設定項目を足したら両方を検討する。
4. **認証情報はコードにも config にも書かない。** 環境変数から読む（`io/db/configs/`）。

## レイヤーと依存方向

```
cli → api → validation / pipeline → data → io
                        metrics → data.bundle
                        report  → pipeline.runner
```

- `io/`: 物理的な入出力（ファイル読み込み、DB、キャッシュ）。`data/` の型を知らない。
- `data/`: カレンダー、ColumnMap（role → 実カラム名）、ソースの読み込み（`loaders.py`）、`DataBundle`。
- `pipeline/preprocess.py`: 期間集約・フォワードリターン・ユニバース適用・`DataBundle` の組み立て。
- `pipeline/runner.py`: metrics の実行（並列時は `DataBundle` を initializer で1回だけ渡す）。失敗は metric 単位で隔離。
- `metrics/`: `DataBundle` → `MetricResult` のみ。データ取得・出力をしない。`_` 始まりのモジュールは自動探索の対象外。
- `report/`: 出力形式の処理（parquet / csv / xlsx / run_info.json）。

## 規約

- 日付キーは文字列（`yyyymmdd` / `yyyymm`）のまま扱う。`asset_id` も文字列に揃える。
- 内部のカラム名は role 名（`date`, `asset_id`, `category`, `factor`, `value`）。定数は `data/columns.py`。
- metric のパラメータは `MetricParams`（pydantic）で定義し、全項目にデフォルト値を持たせる。
  signals / returns / horizons を持つ場合は `SignalReturnParams` を継承すると存在チェックが自動で入る。
- 期間をまたぐリターン集計は `ReturnAggregation`（算術平均と幾何平均を両方出す、累積は kind ごとに選択可）。
- 型エイリアスは `TypeAlias` で書く。コメント・docstring は日本語。

## 未実装・未決事項

- metrics: `factor_exposure`, `residual_ic`, `spread_factor_regression`, `risk_decomposition`, `brinson`
- `portfolios`（config では枠だけ予約。指定するとエラー）、ベンチマークウェイト
- preprocess の整合チェック（トータル ≈ エクスポージャー × ファクターリターン + スペシフィック）
- `factor_covariance` / `specific_risk` はソースの形式のまま読み込んでいる（.dat の具体的な形式が未確定）
- `read_sql` の速度実測（遅ければ chunksize / connectorx）、`report/` の html 出力
