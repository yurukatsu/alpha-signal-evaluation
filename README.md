# alpha-signal-evaluation

株式アルファシグナル（アルファスコア）の評価指標を計算するライブラリ。
IC・分位分析・カテゴリ分析・アルファディケイ・自己相関・ファクター間相関などを、
`config.yaml` を渡す CLI と、DataFrame を直接渡せる Python API の両方から実行できる。

## セットアップ

```sh
uv sync                       # 本体（ファイル入力のみ）
uv sync --extra mssql         # SQL Server を使う場合（ODBC Driver 17 for SQL Server が必要）
uv sync --extra postgres      # PostgreSQL
uv sync --extra mysql         # MySQL
```

DB 接続（`io/db`）は社内の namdb v0.1.2 を取り込んだもの。接続先のホスト名・認証情報は環境変数から読む
（`.env.example` を `.env` にコピーして記入。CLI は実行ディレクトリの `.env` を読み込む）。
config.yaml の DB ソースでは `connection: risk_models` のように接続先 DB 名（小文字）で指定する。

## 使い方

### CLI

```sh
uv run alpha-eval validate config.yaml   # 計算せずに設定・ファイルの存在を検証
uv run alpha-eval run config.yaml        # 評価して output.dir に書き出す（output.formats: csv / parquet / xlsx。既定は csv）
uv run alpha-eval metrics list -v        # 登録済み metric とパラメータ
```

サンプル（いずれも合成データを生成して評価する）:

- [examples/example1](examples/example1): 日次の日付キー（`{Label2}` 等）・one-hot の業種分類
- [examples/example2](examples/example2): 月次（`YYYYMM`）・BARRA 形式の ID・ベンチマーク別ファイル

```sh
uv run python examples/example1/make_sample_data.py && uv run alpha-eval run examples/example1/config.example.yaml
```

### Python API

```python
from alpha_signal_evaluation import evaluate, make_bundle

report = evaluate("config.yaml")
report["ic"].tables["summary"]        # metric ごとの表
report.summary()                      # 全 metric の要約（long 形式）
report.errors                         # 失敗した metric（他の結果は返る）
report.save()                         # config の output 設定で書き出し

# 手元の DataFrame で評価する（io 層をバイパス）
bundle = make_bundle(
    signals,                                  # index=(date, asset_id), columns=シグナル
    {"total": total_ret, "specific": spec_ret},  # 期間リターン（index=date, columns=asset_id）
    return_kinds={"total": "total", "specific": "specific"},
    horizons=[1, 3],
)
report = evaluate({"version": 1, "metrics": [{"name": "ic"}]}, data=bundle)

# metric 単体
from alpha_signal_evaluation.metrics.ic import InformationCoefficient
InformationCoefficient(method="pearson").compute(bundle)
```

## config.yaml

書ける項目をすべて載せた例（コメント付き）は [examples/config.full.yaml](examples/config.full.yaml)、
動く例は [examples/example1/config.example.yaml](examples/example1/config.example.yaml) を参照。要点:

- **カレンダー**（例: `calendar.dat`、1行 = 1評価期間）が時間軸の唯一の定義。`horizons` の単位はカレンダーの行数。
  ヘッダー先頭の `#` は除去し、タブ・スペース・全角スペースが混在していても読める。
  月（`YYYYMM`）だけの1列でもよい（ヘッダー行がなければ `calendar.columns: [Month]` のように列名を指定）。
- ファイルパスはカレンダーの列名をプレースホルダに持つテンプレート（`{Base_day}`, `{Label2}`, `{Trading_day}` 等）。
  どの列でファイルを選んでも、データには `calendar.index` の値が付与される。
- CSV ヘッダー先頭の `#`（`#nri_code`）は除去され、銘柄コードは文字列のまま読む（先頭ゼロを保つ）。
  ヘッダーなし・コメント行付きのファイルは `read_options`（`header: null`, `names`, `comment: "#"` 等）で読む。
- シグナルの `values` はリスト、または `{シグナル名: 列名}`（ソース間で列名が同じ場合）。
- 業種分類が one-hot（`L001`〜`L010` 等）の場合は `one_hot: true`。
- ベンチマークウェイトは `benchmark`（`columns` に `asset_id` と `weight`）で指定する。
  省略時はユニバースの `columns` に `weight` があればそれを使う。
- リターンは名前付き系列の集合。各系列の `kind`（`total` / `specific`）から集約方法が決まる
  （期間内: total は複利・specific は加算）。
- DB ソースは日次で1クエリにまとめて取得し、カレンダー期間に集約する。`match_on` が日付（8桁）なら
  前行の日付の翌日〜当該行の日付、月（6桁）ならその月の1日〜月末日を1期間とする。SQL は `WHERE dt > :start AND dt <= :end`
  のように `:start` / `:end` を参照する（値はパイプラインが自動で渡す。リテラルのコロンは `\:`）。
- 相対パスは config ファイルの場所を基準に解決される。

## 新しい metric を追加する

notebook で試作してからパッケージに組み込むまでの手順は
[notebooks/metric_development_tutorial.ipynb](notebooks/metric_development_tutorial.ipynb) にステップごとにまとめてある。

1. `src/alpha_signal_evaluation/metrics/_template.py` を `metrics/<名前>.py` にコピーして書き換える
2. config.yaml の `metrics` に `- name: <名前>` を追加
3. `uv run pytest tests/metrics/test_contract.py` — 置いたファイルは自動で契約テストの対象になる

本体リポジトリを触らずに追加する場合は、自分のパッケージの `pyproject.toml` に entry point を登録する:

```toml
[project.entry-points."alpha_signal_evaluation.metrics"]
my_metric = "my_package.my_metric"
```

テストには `alpha_signal_evaluation.testing.make_synthetic_bundle()` が使える。

## 組み込み metric

| 名前 | 内容 | requires |
|---|---|---|
| `ic` | IC（spearman / pearson）、Newey-West t 値 | signals, forward_returns |
| `quantile` | 分位ポートフォリオのリターン・スプレッド・累積リターン | signals, forward_returns |
| `category` | カテゴリ内 IC とカテゴリ別平均シグナル | signals, forward_returns, classification |
| `alpha_decay` | ホライズン別の IC / スプレッド | signals, forward_returns |
| `autocorr` | シグナルのクロスセクション自己相関・偏自己相関 | signals |
| `factor_correlation` | シグナル間・シグナル × スタイルエクスポージャーの相関 | signals（risk_models は任意） |

## 開発

```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
```

設計上のルールは [AGENTS.md](AGENTS.md) を参照。
