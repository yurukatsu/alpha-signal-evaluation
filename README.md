# alpha-signal-evaluation

株式アルファシグナル（アルファスコア）の評価指標を計算するライブラリ。
IC・分位分析・カテゴリ分析・アルファディケイ・自己相関・ファクター間相関などを、
`config.yaml` を渡す CLI と、DataFrame を直接渡せる Python API の両方から実行できる。

**マニュアルは [docs/](docs/README.md) にある。** 設計思想、仕様、config.yaml の全項目、組み込み metric、
metric の追加方法、AI チャットに metric を書かせるためのプロンプトをまとめている。この README は要点だけを載せる。

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

出力は metric のテーブルごとの時系列（`<output.dir>/<metric>/<table>.csv`）。`output.by_date: all`
（または日付のリスト）を指定すると、日付ごとの断面も `<output.dir>/by_date/<日付>/<metric>/<table>.csv` に書き出す
（`date` 列を持つ全テーブルをその日付の行だけに切り出したもの。リバランス時点ごとに全シグナルを見比べる用途）。

`alpha-eval run` は metric が1つ終わるたびに結果を書き出すので、途中でプロセスが落ちても（強制終了・Ctrl-C・
データ読み込み中の例外）、それまでに終わった metric の csv / parquet は残る。どこまで終わったかは
`run_info.json` の `state`（`running` のまま = 強制終了、`aborted` = 例外や Ctrl-C で中断、`finished` = 完了）と
`pending`（まだ結果のない metric）で分かる。xlsx は1つのブックなので最後にまとめて書く
（例外や Ctrl-C で中断した場合は、その時点までの分を書く。強制終了では書かれない）。
並列実行（`n_jobs` > 1）中に Ctrl-C で中断した場合、実行中だった metric の結果は残らない。

同じ出力先に再実行すると、`run_info.json` は開始時に、`summary` は metric が終わるたびに今回の内容で置き換わる。
再実行が途中で止まると、まだ終わっていない metric のディレクトリには以前の実行のファイルが残る（`pending` に
載っている metric のファイルは以前の実行のもの）。以前の結果を残したい場合は `--output-dir` で出力先を分ける。

サンプル（いずれも合成データを生成して評価する）:

- [examples/example1](examples/example1): 日次の日付キー（`{Label2}` 等）・one-hot の業種分類
- [examples/example2](examples/example2): 月次（`YYYYMM`）・BARRA 形式の ID・ベンチマーク別ファイル

```sh
uv run python examples/example1/make_sample_data.py && uv run alpha-eval run examples/example1/config.example.yaml
```

`example` / `barra-internal-check` ブランチには生成済みの input があるので、そのまま `alpha-eval run` できる
（`make_sample_data.py` は作り直すときだけ使う。main には input を含めていないので、最初に実行する）。

### Python API

```python
from alpha_signal_evaluation import evaluate, make_bundle

report = evaluate("config.yaml")
report["ic"].tables["summary"]        # metric ごとの表
report.summary()                      # 全 metric の要約（long 形式）
report.errors                         # 失敗した metric（他の結果は返る）
report.save()                         # config の output 設定で書き出し
evaluate("config.yaml", save=True)    # 完了した metric から順に書き出す（途中で落ちても結果が残る。CLI はこの動作）
report.at("20240531")["ic"]["timeseries"]  # ある日付の断面（その日付の全シグナルの行）

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
- リターンのソースの `valid_range`（既定 `[-1, 10]` = -100% 〜 +1000%）の外の値は、DB の特殊値などの
  データの誤りとして欠損にし、件数と例を警告する（DB / Barra は日次、ファイルは期間の値。total・specific とも）。
  統計的な外れ値の処理ではないので、範囲は壊れた値だけを外す緩い値にする（下限は -1 以上）。
- DB ソースは日次で1クエリにまとめて取得し、カレンダー期間に集約する。`match_on` が日付（8桁）なら
  前行の日付の翌日〜当該行の日付、月（6桁）ならその月の1日〜月末日を1期間とする。SQL は `WHERE dt > :start AND dt <= :end`
  のように `:start` / `:end` を参照する（値はパイプラインが自動で渡す。リテラルのコロンは `\:`）。
- 相対パスは config ファイルの場所を基準に解決される。

### Barra（`source: barra`）

社内 DB の Barra（`JPE4` / `GEMLT` / `GEM3`）は、SQL を書かずに `source: barra` で読める。
接続先は `risk_models` と `fs_trfac_glb`（`.env` に両方の設定が必要。`asset_id: bid` なら `risk_models` だけ）。

```yaml
returns:
  - source: barra
    model: JPE4
    match_on: Trading_day          # 期間の境界（日次を期間に集約する）
    currency: local                # USD なら トータルリターンを日次で USD 建てにする
    asset_id: nri_code             # nri_code / bid（省略時: JPE4 は nri_code、GEMLT / GEM3 は bid）
    series: {total: total, specific: specific}   # 省略時もこの2系列

risk_models:
  jpe4:
    source: barra
    model: JPE4
    match_on: Trading_day          # スナップショットの日付と、ファクターリターンの期間の境界
    components: [exposures, factor_covariance, factor_returns]   # 省略時は3つすべて
    specific_return: specific
```

- 単位は読み込み時に揃える: DRTN / SRTN（1日あたりの %）→ 小数、ファクターリターンは小数のまま、
  共分散（年率の %²）→ 年率の小数²。
- 銘柄 ID（`asset_id`）は、日本株だけのモデルは nri_code、日本株を含むグローバルのモデルは BID が基本。
  - `nri_code`（`JPE4` の既定）: BID を `barraid_jp` で nri_code に変換する（`match_on` の日付ごとに、
    その日以前で最新の対応表）。対応のない銘柄は捨てる。
  - `bid`（`GEMLT` / `GEM3` の既定）: 日本株も含めて BID のまま使う。`barraid_jp` は読まず、
    シグナルのある銘柄（ユニバース適用後）だけを DB から取得する。
  - リスクモデルの `asset_id` はエクスポージャーに使う（意味と既定値は returns と同じ）。
- エクスポージャー・共分散は `match_on` の日付以前で最新の Barra の営業日のデータを使う。
- ファクター名は `{model}_FAC` の FAC（`JPE4_` 等の接頭辞を除く）。`factor_groups` は省略すると FGROUP から作る
  （`risk_indices` / `industries` 等。`risk_indices` は `style` としても使える）。

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
| `category_ic` | カテゴリ内 IC とカテゴリ別平均シグナル | signals, forward_returns, classification |
| `category_quantile` | カテゴリ内の分位ポートフォリオ（カテゴリ別とカテゴリ中立のスプレッド） | signals, forward_returns, classification |
| `category_returns` | カテゴリ別の平均・累積リターン（シグナルのある銘柄の等ウェイト） | signals, forward_returns, classification |
| `alpha_decay` | ホライズン別の IC / スプレッド | signals, forward_returns |
| `autocorr` | シグナルのクロスセクション自己相関・偏自己相関 | signals |
| `factor_correlation` | シグナル間・シグナル × スタイルエクスポージャーの相関 | signals（risk_models は任意） |

## 開発

```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
```

設計上のルールは [AGENTS.md](AGENTS.md) を参照。
