# 組み込み metric リファレンス

[マニュアルの目次（README.md）](README.md) に戻る

この文書は、`alpha-eval` に同梱されている 8 個の metric の仕様書です。metric とは、時点合わせ済みのシグナルとリターン一式（`DataBundle`）を受け取り、表と要約値を返す計算の単位です。config.yaml の `metrics` を書く人と、出力された表を読む人に向けて、各 metric の計算方法・パラメータ・出力される表の列を説明します。

- config.yaml 全体の書き方は [03 の 1 章](03_config.md#1-全体の骨格)、出力ファイルの全体像は [02 の 6 章](02_features.md#6-出力)、実行方法は [02 の 7 章](02_features.md#7-使い方) を参照してください。
- metric を自分で追加する方法は [05 の 2 章](05_adding_metrics.md#2-最短手順) にあります。
- シグナル、カレンダー、ホライズン、フォワードリターンなど、マニュアル全体で使う用語は [01 の「この文書で使う用語」](01_design.md#この文書で使う用語) にあります。この文書だけで使う用語は、末尾の [4. 用語](#4-用語) にまとめています。

シグナルは、「値が大きい銘柄ほど将来のリターンが高い」と予想する向きで評価します。予想が当たっていれば、IC と spread（上位 − 下位のリターン差）は正になります。

例に出てくるシグナル名 `ai` / `dss`、リターン系列名 `total` / `specific`、カテゴリ `L001` などは、`examples/example1`（合成データのサンプル。動かし方は [02 の 2 章](02_features.md#2-5分で動かすexample1)）のものです。example1 のカレンダーは1行が1か月なので、ホライズン 1 は「1か月先まで」を意味します。

## 1. 共通事項

### 1.1 config での指定方法

実行する metric は、config.yaml の `metrics` にリストで書きます。リストの1要素が1回の実行で、`name` に metric の名前（[2. metric 一覧](#2-metric-一覧) の name）を、`params` にパラメータを書きます。すべてのパラメータに既定値があるので、`params` は変えたい項目だけを書けば足ります。
同じ metric を別のパラメータでもう一度実行するときは、`id` を付けて結果の名前を分けます（付けないと名前が重複し、config の読み込みでエラーになります）。この文書では、1回の実行を区別する名前（`id`、なければ `name`）を「キー」と呼びます。

```yaml
metrics:
  - name: ic                       # params を省略 = すべて既定値
  - name: quantile
    params: {n_quantiles: 5}
  - name: quantile                 # 同じ metric をもう一度実行する
    id: quantile_q10_specific      # キー。出力のディレクトリ名になる
    params: {n_quantiles: 10, signals: [ai], returns: [specific], horizons: [1]}
```

各項目（`name` / `id` / `enabled` / `params`）の型と既定値は [03 の 8 章](03_config.md#8-metrics) を参照してください。`name` や `params` の誤りは計算を始める前にまとめて報告されます（`uv run alpha-eval validate config.yaml` で、計算せずに検証だけできます）。メッセージの一覧は [03 の 11 章「意味の検証で出るエラー」](03_config.md#意味の検証で出るエラー) にあります。

### 1.2 共通パラメータ: signals / returns / horizons

多くの metric は、評価する対象を次の3つのパラメータで絞れます。どれも省略すると「config にあるもの全部」になります。

| パラメータ | 型 | 既定値 | 意味 |
|---|---|---|---|
| `signals` | 文字列のリスト | `null`（全シグナル） | 評価するシグナル名。config の `data.signals` の `values` で決めた名前 |
| `returns` | 文字列のリスト | `null`（全系列） | 評価するリターン系列名。config の `returns[].series` で定義した名前 |
| `horizons` | 整数のリスト | `null`（全ホライズン） | 評価するホライズン。config の `horizons` に含まれる値だけを指定できる |

- 計算は、対象のシグナル × リターン系列 × ホライズンのすべての組について行われます。出力の表には `signal` / `returns` / `horizon` の列が付き、どの組の値かが分かります。
- `horizons` は書いた順に関わらず昇順で処理されます。ホライズンの単位はカレンダーの行数です。
- `returns` に書くのは、config で定義した系列名です。系列名は自由に付けられ（`total` / `specific` / `excess` / `usd_total` など）、名前によって計算は変わりません。系列名を `gem3_total` と付けた場合は `returns: [gem3_total]` と書きます。
- 存在しない名前や、config の `horizons` にない値を書くと、計算前の検証でエラーになります（[03 の 11 章](03_config.md#意味の検証で出るエラー)）。

3つとも持つのは `ic` / `quantile` / `category_ic` / `category_quantile` / `alpha_decay` です。`category_returns` は `returns` と `horizons` だけ、`autocorr` と `factor_correlation` は `signals` だけを持ちます。

### 1.3 リターンの集計規約: aggregation

`quantile` / `category_quantile` / `category_returns` は、分位ポートフォリオやカテゴリの平均リターンを期間方向に集計します。その規約は3つに共通で、`aggregation` パラメータ（コード上は `ReturnAggregation`）で表します。

**平均は算術平均と幾何平均の両方を出します。** どちらかを選ぶ設定はありません。

| 列 | 計算 | 用途 |
|---|---|---|
| `arith_mean` | 欠損を除いた単純平均 | 予測力の評価。t 値はこの平均についてのもの |
| `geo_mean` | `expm1(mean(log1p(r)))`（複利で均した平均） | 実際に運用したときの成長率の目安 |

- 1つでも −100% 以下のリターンがあると、`geo_mean` は欠損になります。
- 平均は h 期間リターンのままの値です。1期間あたりへの換算はしません（ホライズン間で比べるときは `alpha_decay` の `spread_per_period` を使います）。

**累積リターンは、既定ではリターン系列の `aggregation` で計算します。** `aggregation` は、`source: file` と `source: db` の `returns[].series` に系列ごとに書く必須の項目です（[03 の 6 章](03_config.md#6-returns)）。系列名は計算に影響しません。

| `aggregation` | 束ね方 | 累積リターンの計算 |
|---|---|---|
| `cumprod` | 複利 | `(1 + r).cumprod() − 1` |
| `cumsum` | 単純加算 | `r.cumsum()` |

example1 の系列は、`total`（トータルリターン）が `cumprod`、`specific`（スペシフィックリターン。リスクモデルのファクターで説明されない、銘柄固有の部分）が `cumsum` です。

系列の `aggregation` は、日次から期間への集約（`source: db` と `source: barra`。`source: file` は集約しません）、フォワードリターンの計算（[02 の 4.4](02_features.md#44-フォワードリターンの定義)）、`alpha_decay` の1期間あたりへの換算にも使います。`source: barra` の系列では `aggregation` を選べません。`series` の値で決まり、`total`（DRTN）は `cumprod`、`specific`（SRTN）は `cumsum` です（[03 の 6 章「source: barra のリターン」](03_config.md#source-barra-のリターン)）。

累積リターンの方法だけを metric ごとに変えるときは、`aggregation.cumulative` に `{系列名: 方法}` の形で `cumprod` か `cumsum` を書きます。既定は `{}` で、書かなかった系列は系列自身の `aggregation` を使います。

```yaml
  - name: quantile
    params:
      aggregation:
        cumulative: {specific: cumprod}   # specific の累積だけ複利にする。他の系列は変わらない
```

- `aggregation.cumulative` で変わるのは累積リターンの表（`cumulative`。`category_quantile` では `neutral_cumulative` も）だけです。フォワードリターンと、`returns` / `summary` などの他の表は変わりません。
- config にない系列名を書くと、計算前の検証で `aggregation.cumulative: unknown return series [...]; available: [...]` のエラーになります。
- 累積リターンを出すのはホライズン 1 だけです。ホライズンが 2 以上のフォワードリターンは隣の行と期間が重なるので、累積すると同じ期間を何度も数えてしまうためです。
- リターンが欠損の行は、累積リターンも欠損になります。累積そのものは欠損の行を飛ばして続きます。評価期間の末尾で、先のリターンがまだないためにフォワードリターンが欠損の行も同じ扱いです。

### 1.4 t 値の計算方法

`t_stat` という列（`alpha_decay` では `ic_t_stat` / `spread_t_stat`）はすべて、「時系列の平均が 0 と異なるか」を見る t 値です。標準誤差には Newey-West 推定を使い、ラグは `horizon − 1` にしています。

ラグを `horizon − 1` にする理由は、ホライズンの重なりです。行 t のフォワードリターンは「行 t から h 行先まで」のリターンなので、隣り合う行では h − 1 期間が重なります。その結果、IC や spread の時系列には h − 1 行先まで系列相関が生じます。通常の t 値はこの相関を無視するため、h が大きいほど過大になります。Newey-West はこの系列相関を標準誤差に織り込みます。

計算は次のとおりです。

1. 時系列から欠損を除きます。残った n 個の値の平均を m、平均からの偏差を e とします。
2. ラグ k の自己共分散を γ(k) = (1/n) Σ e(t)·e(t−k) とします（n で割ります）。
3. 長期分散を S = γ(0) + 2 Σ w(k)·γ(k) とします。和は k = 1 から L = `horizon − 1` まで、重みは w(k) = 1 − k/(L + 1) です（Bartlett カーネル）。
4. t 値は m / √(S / n) です。

- ホライズン 1 では L = 0 なので、通常の t 値（標準偏差は n で割るもの）と一致します。
- 欠損は先に除くので、ラグは欠損を詰めた後の並びで数えます。n − 1 を超えるラグの項は和に入りません。
- 有効な値が 2 個未満のとき、または標準誤差が 0 のとき、t 値は欠損です。

### 1.5 出力の共通ルール

metric の結果は、次の3つで構成されます。

| 要素 | 内容 | 書き出し先（csv の場合） |
|---|---|---|
| tables | 名前付きの表。時系列や、組ごとの要約 | `<output.dir>/<キー>/<表の名前>.csv` |
| summary | `名前 → 数値` の要約（名前は `summary.csv` の `stat` 列になる）。全 metric を横断して見比べるためのもの | 全 metric をまとめて `<output.dir>/summary.csv` |
| notes | 計算方法の注記（英文） | `run_info.json` の `notes` |

`<キー>` は [1.1](#11-config-での指定方法) で説明した実行のキー（`id`、なければ `name`）です。出力先と形式（csv / parquet / xlsx）の指定は [03 の 9 章 output](03_config.md#output) を参照してください。

example1 を同梱の `config.example.yaml` のまま実行したときの出力の一部です。

```text
output/
├── ic/
│   ├── timeseries.csv
│   └── summary.csv
├── quantile_q10_specific/        # id を付けた実行は id がディレクトリ名
│   ├── returns.csv
│   ├── cumulative.csv
│   └── summary.csv
├── summary.csv                   # 全 metric の summary
├── run_info.json
└── by_date/20240531/ic/timeseries.csv   # output.by_date を指定した場合
```

`summary.csv` は1行が1つの数値で、列は `metric`（キー）・`stat`・`value` です。次は、上と同じ実行の `summary.csv` から3行を抜き出したものです。

```text
metric,stat,value
ic,ai/total/h1/mean_ic,0.08431602966900215
ic,ai/total/h1/t_stat,17.609236611658936
quantile,ai/total/h1/arith_mean,0.018480068424499528
```

`stat` は `/` 区切りで、形は metric によって次の3通りです。

| 形 | 例 | 使う metric |
|---|---|---|
| `{signal}/{returns}/h{horizon}/{統計量}` | `ai/total/h1/mean_ic` | `ic` / `quantile` / `category_quantile` / `alpha_decay` |
| `{signal}/lag{lag}/acf` | `ai/lag1/acf` | `autocorr` |
| `{a}/{b}/mean_corr` | `ai/dss/mean_corr` | `factor_correlation` |

`category_ic` と `category_returns` は summary を持ちません（結果は表だけです）。

そのほかの共通ルールは次のとおりです。

- **日付ごとの断面**: config で `output.by_date` を指定すると、`date` 列を持つ表が日付ごとに切り出され、`by_date/<日付>/<キー>/<表の名前>.csv` にも書かれます（`date` 列は除かれます）。対象になるかどうかは `date` 列の有無だけで決まります。`summary`・`decay`・`pairs`・`matrix` のように `date` 列のない表は対象外です。指定のしかたは [02 の 6.4](02_features.md#64-日付ごとの断面outputby_date) を参照してください。
- **欠損**: 計算できなかった値は欠損のまま出力します（0 で埋めません）。csv では空欄になります。
- **等ウェイト**: 分位ポートフォリオやカテゴリの平均リターンは、すべて銘柄の等ウェイト平均です。ユニバースやベンチマークのウェイトは使いません。
- **失敗の扱い**: ある metric が計算中に例外で失敗しても、他の metric の結果は出力されます。失敗した metric は `run_info.json` の `status` に `failed` と記録され、トレースバックが `errors` に入ります。`alpha-eval run` の終了コードは 1 になります（[02 の 6.3](02_features.md#63-run_infojson)）。
- **書き出しのタイミング**: `alpha-eval run` と `evaluate(save=...)` は、metric が1つ終わるたびに、その metric の表と日付ごとの断面を csv / parquet で書き、`summary` と `run_info.json` を更新します。xlsx は最後に（例外や Ctrl-C で中断したときはその時点で）まとめて書きます。実行が途中で止まったときに何が残るかは [02 の 6.5](02_features.md#65-逐次書き出し途中で落ちたときに残るもの) を参照してください。

### 1.6 Python から metric を単体で使う

metric は config を通さずに、`DataBundle` に対して直接実行できます。次の例は、同梱の合成データ（シグナル `alpha` / `noise` / `mixed`）で `ic` を計算します。

```python
from alpha_signal_evaluation.metrics import get_metric
from alpha_signal_evaluation.testing import make_synthetic_bundle

bundle = make_synthetic_bundle()
result = get_metric("ic")(method="pearson", horizons=[1]).compute(bundle)

result.tables["summary"]                      # DataFrame
result.summary["alpha/specific/h1/mean_ic"]   # float
result.notes                                  # 注記のリスト
```

config を使って実行した場合は、`evaluate()` の戻り値から `report["ic"].tables["summary"]` のようにキーで取り出します（[02 の 7.2](02_features.md#72-python-api)）。`DataBundle` の中身は [05 の 5 章](05_adding_metrics.md#5-databundle-の使い方) にあります。

## 2. metric 一覧

登録されている metric は次の 8 個です。名前をクリックすると、3 章の仕様に移動します。

| name | 何を測るか | 必要なデータ（requires） | 出力する表 |
|---|---|---|---|
| [`ic`](#ic) | シグナルとフォワードリターンのクロスセクション相関（IC） | signals, forward_returns | `timeseries`, `summary` |
| [`quantile`](#quantile) | シグナルの分位ポートフォリオのリターンと、上位 − 下位の spread | signals, forward_returns | `returns`, `cumulative`, `summary` |
| [`category_ic`](#category_ic) | カテゴリ（業種など）の中での IC と、カテゴリ別の平均シグナル | signals, forward_returns, classification | `ic_timeseries`, `summary`, `mean_signal` |
| [`category_quantile`](#category_quantile) | カテゴリの中で分けた分位ポートフォリオと、カテゴリ中立の spread | signals, forward_returns, classification | `returns`, `cumulative`, `summary`, `neutral_returns`, `neutral_cumulative`, `neutral_summary` |
| [`category_returns`](#category_returns) | カテゴリ別の平均リターンと累積リターン（シグナルの値は使わない） | signals, forward_returns, classification | `returns`, `cumulative`, `summary` |
| [`alpha_decay`](#alpha_decay) | ホライズン別の IC と spread（予測力の減衰） | signals, forward_returns | `decay` |
| [`autocorr`](#autocorr) | シグナルの自己相関と偏自己相関（シグナルの持続性） | signals | `timeseries`, `summary` |
| [`factor_correlation`](#factor_correlation) | シグナル同士、およびシグナルとリスクモデルのエクスポージャーの相関 | signals（リスクモデルは任意） | `pairs`, `matrix` |

「必要なデータ」は config のセクションに対応します。signals は `data.signals`、forward_returns は `returns`（1つ以上）、classification は `data.classification` です。足りない場合は計算前の検証でエラーになります。

登録されている metric の名前と、パラメータの名前・既定値は、次のコマンドでも確認できます。

```sh
uv run alpha-eval metrics list       # 名前・説明・必要なデータ・パラメータの既定値
uv run alpha-eval metrics list -v    # パラメータの型と制約（JSON Schema）
```

ここにない metric（ファクターエクスポージャー、リスク分解、Brinson 型の要因分解など）は未実装です。config の `name` に書くと `Unknown metric` のエラーになります。予定されているものは [02 の 9 章](02_features.md#9-現時点でできないこと未実装) に挙げています。

## 3. metric ごとの仕様

metric ごとに、計算方法・パラメータ・出力・読み方と注意点・config の例の順に説明します。パラメータ表の `signals` / `returns` / `horizons` は [1.2](#12-共通パラメータ-signals--returns--horizons)、`aggregation` は [1.3](#13-リターンの集計規約-aggregation)、t 値は [1.4](#14-t-値の計算方法) のとおりです。「summary のキー」は、`summary.csv` の `stat` 列に出る名前です（[1.5](#15-出力の共通ルール)）。

### ic

シグナルと、同じ行のフォワードリターンとのクロスセクション相関（IC）を日付ごとに求め、その時系列を要約します。シグナルが銘柄の将来リターンの大小をどれだけ言い当てているかを見る、最も基本的な指標です。

**計算方法**（シグナル × リターン系列 × ホライズンの組ごと）

1. シグナルとフォワードリターンの両方がそろっている (日付, 銘柄) を取り出します。
2. 日付ごとに、銘柄方向の相関を求めます。`spearman` は日付内で両者を順位（同順位は平均順位）に直してから相関を取ります。`pearson` は値そのものの相関です。
3. 銘柄数が `min_obs` 未満の日付と、相関が定義できない日付（値がすべて同じなど）は欠損にします。
4. IC の時系列から、平均・標準偏差・t 値・正の割合・期間数を求めます。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` / `returns` / `horizons` | リスト | `null` | 共通パラメータ |
| `method` | `spearman` / `pearson` | `spearman` | 相関の種類 |
| `min_obs` | 整数 | `10` | IC を計算する最小銘柄数。3 以上 |

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `timeseries` | 日付（評価期間のすべてのカレンダー行） | `date`, `ic`, `n`, `signal`, `returns`, `horizon` |
| `summary` | (signal, returns, horizon) | `signal`, `returns`, `horizon`, `mean_ic`, `std_ic`, `t_stat`, `hit_rate`, `n_periods` |

| 列 | 内容 |
|---|---|
| `date` | 日付キー（その行の、カレンダーの `calendar.index` の列の値。example1 では `20240531` のような8桁の日付）。他の metric の `date` 列も同じ |
| `ic` | その日付の IC |
| `n` | その日付の銘柄数（`min_obs` 未満でも出る。データが1件もない日付は欠損） |
| `signal` / `returns` / `horizon` | どの組の値か |
| `mean_ic` / `std_ic` | IC の平均 / 標準偏差（n − 1 で割る） |
| `t_stat` | 平均 IC の Newey-West t 値 |
| `hit_rate` | IC が正だった期間の割合 |
| `n_periods` | IC が欠損でない期間数 |

summary のキー: `{signal}/{returns}/h{horizon}/mean_ic` と `.../t_stat`。

**読み方と注意点**

- 評価期間の末尾で、h 行先までのリターンがまだない行は、IC が欠損になり `n_periods` に数えられません。
- 銘柄数の少ないユニバースでは、`min_obs` を下回る日付がすべて欠損になります。`timeseries` の `n` で確認できます。

**config の例**

```yaml
  - name: ic
    id: ic_pearson
    params: {method: pearson, min_obs: 30, horizons: [1, 3]}
```

### quantile

日付ごとにシグナルの大きさで銘柄を分位に分け、分位ごとの等ウェイトポートフォリオのフォワードリターンと、上位 − 下位の spread を求めます。シグナルどおりに銘柄を選んだときのリターンの形を見るためのものです。

**計算方法**（シグナル × リターン系列 × ホライズンの組ごと）

1. シグナルとフォワードリターンの両方がそろっている (日付, 銘柄) を取り出します。
2. `demean: true` の場合、日付ごとに、手順 1 で取り出した全銘柄の平均リターンを各銘柄のリターンから引きます。
3. 日付ごとにシグナルの順位で分位を割り当てます。Q1 がシグナル最小、Q{n} が最大です。各分位の銘柄数はほぼ均等で、差は最大 1 銘柄です（例: 7 銘柄を 5 分位に分けると 2, 1, 2, 1, 1）。同じ値の銘柄はデータの並び順で分けます。銘柄数が分位数に満たない日付は除きます。
4. 日付 × 分位ごとに、フォワードリターンの等ウェイト平均を取ります。
5. 日付ごとに spread = Q{n} − Q1 を求めます。
6. Q1〜Q{n} と spread のそれぞれの時系列について、算術平均と幾何平均を求めます。spread には t 値と、正だった期間の割合も求めます。
7. ホライズン 1 のときだけ、Q1〜Q{n} と spread の累積リターンを作ります。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` / `returns` / `horizons` | リスト | `null` | 共通パラメータ |
| `n_quantiles` | 整数 | `5` | 分位数。2 以上 |
| `demean` | 真偽値 | `false` | `true` なら日付ごとの平均を引いた超過リターンで集計する |
| `aggregation` | 辞書 | `{cumulative: {}}` | 系列名ごとの累積リターンの方法。書かなかった系列は系列の `aggregation` を使う（[1.3](#13-リターンの集計規約-aggregation)） |

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `returns` | 日付 × 分位（データのある組だけ） | `date`, `quantile`, `mean_return`, `n`, `signal`, `returns`, `horizon` |
| `cumulative` | 日付 × ポートフォリオ（ホライズン 1 だけ。評価期間のすべてのカレンダー行） | `date`, `portfolio`, `cumulative_return`, `signal`, `returns` |
| `summary` | (signal, returns, horizon, ポートフォリオ) | `signal`, `returns`, `horizon`, `portfolio`, `arith_mean`, `geo_mean`, `t_stat`, `hit_rate` |

| 列 | 内容 |
|---|---|
| `quantile` | 分位（1 〜 `n_quantiles` の整数） |
| `mean_return` | その分位の等ウェイト平均フォワードリターン（h 期間） |
| `n` | その分位の銘柄数 |
| `portfolio` | `Q1` 〜 `Q{n}` と `spread` |
| `cumulative_return` | その行までの累積リターン |
| `arith_mean` / `geo_mean` | h 期間リターンの算術平均 / 幾何平均 |
| `t_stat` | spread の Newey-West t 値（spread の行だけ。他は欠損） |
| `hit_rate` | spread が正だった期間の割合（spread の行だけ。他は欠損） |

summary のキー: spread について `{signal}/{returns}/h{horizon}/arith_mean`、`.../geo_mean`、`.../t_stat`。

**読み方と注意点**

- `demean` を切り替えても spread は変わりません（上位と下位から同じ平均を引くためです）。変わるのは Q1〜Q{n} の値で、`true` にすると各分位がユニバース平均をどれだけ上回ったかになります。
- `cumulative` の spread は、日付ごとの spread を1本のリターン系列として累積したものです。複利（`cumprod`）の場合、Q{n} の累積と Q1 の累積の差とは一致しません。
- example1 の `total`（`cumprod`）と `specific`（`cumsum`）は累積の方法が違うので、両者の累積リターンの差はファクター寄与と厳密には一致しません。

**config の例**

```yaml
  - name: quantile
    id: quantile_demean
    params:
      n_quantiles: 10
      demean: true
      aggregation: {cumulative: {specific: cumprod}}
```

### category_ic

カテゴリ（業種など）の中だけで銘柄を比べた IC と、カテゴリ別の平均シグナルを求めます。シグナルの予測力が特定のカテゴリに偏っていないか、シグナルそのものが特定のカテゴリに傾いていないかを見ます。

以下、カテゴリ系の3つの metric の表で `<column>` と書いた列は、`column` パラメータの値（既定は `category`）が列名になります。

**計算方法**

1. カテゴリ内 IC（シグナル × リターン系列 × ホライズンの組ごと）: `ic` と同じデータに分類を結合し、日付 × カテゴリごとに相関を求めます。分類のない銘柄は除きます。`spearman` の順位は日付 × カテゴリの中で付けます。銘柄数が `min_obs` 未満の日付 × カテゴリは欠損にします。
2. カテゴリごとの IC の時系列について、平均と t 値を求めます。
3. カテゴリ別の平均シグナル（シグナルごと。リターンやホライズンには依りません）: 日付 × カテゴリごとに、シグナルの平均と銘柄数を求めます。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` / `returns` / `horizons` | リスト | `null` | 共通パラメータ |
| `method` | `spearman` / `pearson` | `spearman` | 相関の種類 |
| `min_obs` | 整数 | `5` | 日付 × カテゴリごとに IC を計算する最小銘柄数。3 以上（`ic` の既定値 10 より小さい） |
| `column` | 文字列 | `category` | カテゴリとして使う分類データの列名 |

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `ic_timeseries` | 日付 × カテゴリ（データのある組だけ） | `date`, `<column>`, `ic`, `n`, `signal`, `returns`, `horizon` |
| `summary` | (signal, returns, horizon, カテゴリ) | `signal`, `returns`, `horizon`, `<column>`, `mean_ic`, `t_stat`, `n_periods`, `avg_n_assets` |
| `mean_signal` | 日付 × カテゴリ | `date`, `<column>`, `mean_signal`, `n`, `signal` |

- `n` はその日付 × カテゴリの銘柄数、`n_periods` は IC が欠損でない期間数です。
- `avg_n_assets` は、そのカテゴリが現れた日付の平均銘柄数です（IC が欠損の日付も含みます）。

summary のキー: ありません（結果は表だけです）。

**読み方と注意点**

- `mean_signal` はシグナルの生の値の平均です。ユニバース平均との差ではないので、シグナル全体の水準が 0 でない場合は、カテゴリ間の差として読みます。
- 分類を `one_hot: true` で読み込んだ場合、列は `category` だけです。縦持ちで読み込んだ場合（example1）は、`columns` で `category` に対応付けた列が `category` になり、ソースにあった他の列も元の名前のまま残ります。その列名を `column` に指定すると、その列をカテゴリとして同じ分析ができます。
- `column` に存在しない列を書いた場合は、計算前の検証では検出されず、計算時にこの metric だけが `KeyError` で失敗します。

**config の例**

```yaml
  - name: category_ic
    params: {returns: [specific], horizons: [1], min_obs: 5}
```

### category_quantile

カテゴリの中でシグナルの分位を割り当て、カテゴリごとの分位ポートフォリオと、全カテゴリを合わせた「カテゴリ中立」の分位ポートフォリオを求めます。カテゴリの偏りを除いたうえで、シグナルに銘柄選択の効果があるかを見ます。

**計算方法**（シグナル × リターン系列 × ホライズンの組ごと）

1. `quantile` と同じデータに分類を結合します。分類のない銘柄は除きます。
2. 日付 × カテゴリの中で、シグナルの順位から分位を割り当てます（割り当て方は `quantile` と同じです）。銘柄数が分位数に満たない日付 × カテゴリは除きます。
3. カテゴリ別: カテゴリごとに、そのカテゴリの銘柄だけで `quantile` の手順 4〜7 を行います。
4. カテゴリ中立: 手順 2 で付けた分位のまま全カテゴリの銘柄を合わせて、`quantile` の手順 4〜7 を行います。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` / `returns` / `horizons` | リスト | `null` | 共通パラメータ |
| `n_quantiles` | 整数 | `5` | カテゴリ内の分位数。2 以上 |
| `column` | 文字列 | `category` | カテゴリとして使う分類データの列名 |
| `aggregation` | 辞書 | `{cumulative: {}}` | 系列名ごとの累積リターンの方法。書かなかった系列は系列の `aggregation` を使う（[1.3](#13-リターンの集計規約-aggregation)） |

**出力**

各列の意味は [`quantile`](#quantile) の同名の表と同じです。

| 表 | 内容 | 列 |
|---|---|---|
| `returns` | カテゴリ別の、日付 × 分位ごとの平均リターン | `date`, `quantile`, `mean_return`, `n`, `signal`, `returns`, `horizon`, `<column>` |
| `cumulative` | カテゴリ別の、ホライズン 1 の累積リターン | `date`, `portfolio`, `cumulative_return`, `signal`, `returns`, `<column>` |
| `summary` | カテゴリ別の要約 | `signal`, `returns`, `horizon`, `<column>`, `portfolio`, `arith_mean`, `geo_mean`, `t_stat`, `hit_rate` |
| `neutral_returns` / `neutral_cumulative` / `neutral_summary` | カテゴリ中立の結果 | 上の3つから `<column>` を除いたもの |

summary のキー: カテゴリ中立の spread について `{signal}/{returns}/h{horizon}/arith_mean`、`.../geo_mean`、`.../t_stat`。

**読み方と注意点**

- カテゴリ中立のポートフォリオは銘柄ごとの等ウェイトです。カテゴリごとに同じ重みにはしないので、銘柄数の多いカテゴリほど結果への影響が大きくなります。
- 銘柄数の少ないカテゴリがある場合は、`n_quantiles` を小さくします。分位数に満たない日付 × カテゴリは、カテゴリ中立の集計からも除かれます。すべて除かれた場合、要約の値は欠損になります。
- `column` の注意点は `category_ic` と同じです。

**config の例**

```yaml
  - name: category_quantile
    params: {n_quantiles: 3, returns: [specific], horizons: [1]}
```

### category_returns

評価対象の銘柄について、カテゴリ別の等ウェイト平均フォワードリターンと累積リターンを求めます。シグナルの値は使いません。期間中にどのカテゴリが上がったかを示すので、`category_ic` の `mean_signal`（シグナルのカテゴリへの傾き）と並べて、カテゴリの偏りが成績にどう効いたかを読むために使います。

**計算方法**（リターン系列 × ホライズンの組ごと）

1. 評価対象の (日付, 銘柄) を決めます。いずれかのシグナルに値がある組が対象です（ユニバースで絞った後の銘柄です）。
2. 対象のフォワードリターンに分類を結合し、日付 × カテゴリごとに等ウェイト平均と銘柄数を求めます。分類のない銘柄は除きます。
3. カテゴリごとの時系列について、算術平均と幾何平均を求めます。
4. ホライズン 1 のときだけ、カテゴリごとの累積リターンを作ります。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `returns` / `horizons` | リスト | `null` | 共通パラメータ（`signals` はありません） |
| `column` | 文字列 | `category` | カテゴリとして使う分類データの列名 |
| `aggregation` | 辞書 | `{cumulative: {}}` | 系列名ごとの累積リターンの方法。書かなかった系列は系列の `aggregation` を使う（[1.3](#13-リターンの集計規約-aggregation)） |

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `returns` | 日付 × カテゴリ（データのある組だけ） | `date`, `<column>`, `mean_return`, `n`, `returns`, `horizon` |
| `cumulative` | 日付 × カテゴリ（ホライズン 1、すべてのカレンダー行） | `date`, `<column>`, `cumulative_return`, `returns` |
| `summary` | (returns, horizon, カテゴリ) | `returns`, `horizon`, `<column>`, `arith_mean`, `geo_mean`, `avg_n_assets` |

- `mean_return` はその日付 × カテゴリの等ウェイト平均フォワードリターン（h 期間）、`n` はその銘柄数です。
- `avg_n_assets` は、そのカテゴリが現れた日付の平均銘柄数です。

summary のキー: ありません（結果は表だけです）。

**読み方と注意点**

- シグナルは評価対象の銘柄を決めるためだけに使います。config にあるシグナルのどれにも値がない銘柄は、リターンがあっても含まれません。
- t 値は出しません。この metric はシグナルの評価ではなく、背景となる値動きの確認用です。

**config の例**

```yaml
  - name: category_returns
    params: {horizons: [1]}
```

### alpha_decay

ホライズンごとの IC と分位 spread を1つの表に並べます。シグナルの予測力が、先の期間に向かってどう弱まるか（減衰）を見ます。

**計算方法**（シグナル × リターン系列 × ホライズンの組ごと）

1. `ic` と同じ方法で日付ごとの IC を求め、平均と t 値を取ります。
2. `quantile` と同じ方法で分位を割り当て、日付ごとに spread = Q{n} − Q1 を求め、平均と t 値を取ります。
3. `spread_per_period` を求めます。Q{n} と Q1 のそれぞれについて、h 期間リターンの時系列平均を1期間あたりに換算し、その差を取ります。換算方法は系列の `aggregation` で決まります（`cumprod`: `(1 + r)^(1/h) − 1`、`cumsum`: `r / h`）。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` / `returns` / `horizons` | リスト | `null` | 共通パラメータ |
| `method` | `spearman` / `pearson` | `spearman` | IC の相関の種類 |
| `n_quantiles` | 整数 | `5` | spread に使う分位数。2 以上 |
| `min_obs` | 整数 | `10` | IC を計算する最小銘柄数。3 以上。spread には適用されない |

**出力**

`decay`: 1行が (signal, returns, horizon) の1組。`date` 列がないので、日付ごとの断面には出ません。

| 列 | 内容 |
|---|---|
| `signal` / `returns` / `horizon` | どの組の値か |
| `mean_ic` / `ic_t_stat` | IC の平均と Newey-West t 値 |
| `mean_spread` / `spread_t_stat` | spread（h 期間リターンのまま）の平均と Newey-West t 値 |
| `spread_per_period` | 1期間あたりに換算した spread |

summary のキー: `{signal}/{returns}/h{horizon}/mean_ic` と `.../spread_per_period`。

**読み方と注意点**

- ホライズン間で spread を比べるときは `spread_per_period` を使います。`mean_spread` は h 期間分のリターンなので、h が大きいほど大きくなるのが普通です。下は、example1 を同梱の `config.example.yaml` のまま実行したときの `ai` × `total` の結果です（同梱の config は `alpha_decay` に `params` を書いていないので、`n_quantiles` は既定の 5 です）。`mean_spread` は増えていますが、1期間あたりでは減っています。

  | horizon | mean_ic | mean_spread | spread_per_period |
  |---|---|---|---|
  | 1 | 0.084 | 0.0185 | 0.0185 |
  | 3 | 0.112 | 0.0418 | 0.0136 |
  | 6 | 0.108 | 0.0609 | 0.0096 |

- 減衰を見るには、config の `horizons` に複数の値（例: `[1, 3, 6]`）が必要です。
- `spread_per_period` の換算方法は系列の `aggregation` で決まります。`alpha_decay` に `aggregation` パラメータはないので、metric 側では変更できません。

**config の例**

```yaml
  - name: alpha_decay
    params: {n_quantiles: 10}
```

### autocorr

シグナルの銘柄の並びが、カレンダー上の k 行前とどれだけ似ているかを、自己相関（ACF）と偏自己相関（PACF）で測ります。シグナルの持続性の目安です。自己相関が高いシグナルは銘柄の入れ替わりが遅く、低いシグナルは速くなります。リターンは使いません。

**計算方法**（シグナルごと）

1. シグナルを 日付 × 銘柄 の表にします。行は評価期間のすべてのカレンダー行です。
2. ラグ k = 1 〜 `lags` の最大値について、行 t と行 t − k の銘柄方向の相関を行ごとに求めます。両方の行にシグナルがある銘柄だけを使います。`spearman` はその共通の銘柄の中で順位に直してから相関を取ります。共通の銘柄が `min_obs` 未満の行は欠損にします。
3. ラグ k の ACF は、手順 2 で求めた時系列の平均です。
4. PACF は、ラグ 1 〜 最大ラグの ACF から Durbin-Levinson の再帰で求めます。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` | リスト | `null` | 共通パラメータ |
| `lags` | 整数のリスト | `[1, 2, 3, 6, 12]` | 出力するラグ（カレンダーの行数）。空にできない。すべて 1 以上。重複は除かれ、昇順に並ぶ |
| `method` | `spearman` / `pearson` | `spearman` | 相関の種類 |
| `min_obs` | 整数 | `10` | 行 t と t − k の両方にシグナルがある銘柄の最小数。3 以上 |

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `timeseries` | 日付 × ラグ（`lags` のラグだけ。評価期間のすべてのカレンダー行） | `date`, `autocorr`, `n`, `signal`, `lag` |
| `summary` | (signal, lag) | `signal`, `lag`, `acf`, `pacf` |

- `autocorr` は行 t（`date`）と行 t − k の相関、`n` は両方の行にシグナルがある銘柄数です。

summary のキー: `{signal}/lag{lag}/acf`。

**読み方と注意点**

- ラグの単位はカレンダーの行数です。example1 では 1 行が 1 か月なので、`lag = 12` は 12 か月前との比較です。
- 評価期間の最初の k 行は、比べる相手の行がないので欠損です（`n` は 0）。評価期間より前のシグナルは使いません。ラグが評価期間の行数以上の場合、そのラグの ACF と PACF は欠損になります。
- PACF は「ラグ 1 〜 k − 1 の影響を除いたラグ k の相関」です。時系列平均した ACF から求めた値で、銘柄ごとの時系列の PACF ではありません。そのため `lags` にないラグの ACF も内部で計算しています。途中のラグの ACF が欠損の場合、そのラグ以降の PACF は欠損になります。

**config の例**

```yaml
  - name: autocorr
    params: {lags: [1, 3, 6, 12]}
```

### factor_correlation

シグナル同士、およびシグナルとリスクモデルのファクターエクスポージャーとの、クロスセクション相関の時系列平均を求めます。シグナル同士がどれだけ重複しているか、シグナルが既知のファクター（スタイルなど）とどれだけ似ているかを見ます。リターンは使いません。

**計算方法**

1. 対象のシグナルを取り出します。下の「エクスポージャーを加える条件」を満たす場合は、エクスポージャーを (日付, 銘柄) で結合します。このとき対象は、エクスポージャーのある銘柄だけになります。
2. `spearman` の場合、各変数を日付内で順位（同順位は平均順位）に直します。
3. すべてのシグナルについて、欠損でない銘柄数が `min_obs` 以上ある日付だけを残します。
4. 日付ごとに、全変数（シグナル + ファクター）の相関行列を求めます。組ごとに、両方が欠損でない銘柄を使います。
5. シグナルを片方に含む組について、相関の時系列の平均・標準偏差・期間数を求めます。また、相関行列を日付で平均します。

**パラメータ**

| パラメータ | 型 | 既定値 | 意味・制約 |
|---|---|---|---|
| `signals` | リスト | `null` | 共通パラメータ |
| `method` | `spearman` / `pearson` | `spearman` | 相関の種類 |
| `include_exposures` | 真偽値 | `true` | リスクモデルのエクスポージャーを相関の対象に加えるか |
| `risk_model` | 文字列 | `null` | エクスポージャーを使うリスクモデルの名前（config の `risk_models` のキー）。省略時は、リスクモデルが1つだけならそれを使う。config にない名前を書くと、計算前の検証でエラーになる |
| `factor_group` | 文字列 | `style` | 使うファクターのグループ名。リスクモデルの `factor_groups` にあるグループのファクターだけに絞る。`null` なら全ファクター |
| `min_obs` | 整数 | `10` | 日付ごとに必要な、各シグナルの欠損でない銘柄数。3 以上 |

エクスポージャーは、次の条件をすべて満たすときに加わります。満たさない場合はエラーにならず、シグナルだけで計算します。

- `include_exposures` が `true` です。
- config にリスクモデルがあります。
- `risk_model` を指定しているか、リスクモデルが1つだけです。
- そのリスクモデルにエクスポージャーがあります。

**出力**

| 表 | 1行の単位 | 列 |
|---|---|---|
| `pairs` | 変数の組 (a, b)。a はシグナル、b はシグナルまたはファクター | `a`, `b`, `mean_corr`, `std_corr`, `n_periods` |
| `matrix` | 変数（日付で平均した相関行列。正方行列） | 全変数（シグナル + ファクター） |

- `pairs` では、シグナル同士の組は (a, b) と (b, a) の両方が出ます。`mean_corr` / `std_corr` は相関の時系列の平均 / 標準偏差（n − 1 で割る）、`n_periods` は相関が欠損でない日付数です。
- `matrix` の csv では、1列目（見出しは空）が行の変数名になります。

summary のキー: シグナル同士の組について `{a}/{b}/mean_corr`。同じ組が2回出ないよう、名前の辞書順で a < b の組だけです。

**読み方と注意点**

- リスクモデルの `factor_groups` に `factor_group` で指定したグループがない場合、エラーにはならず、絞り込まずに全ファクターを使います。業種ファクターまで含めたくない場合は、`style` というグループがあるかを確認してください。`source: barra` のリスクモデルでは、`factor_groups` を省略すると Barra のファクター一覧からグループが作られ、`risk_indices` というグループがあれば `style` という名前でも参照できます。ファイルや DB から読むリスクモデルでは、config の `factor_groups` に自分で定義します（[03 の 7 章「構成要素をソースで定義する」](03_config.md#構成要素をソースで定義する)）。
- リスクモデルが複数あるのに `risk_model` を指定していない場合、エクスポージャーは加わりません。`pairs` にファクターが出ないときは、まずこの点を確認します。
- `spearman` では、順位は変数ごとにその変数が欠損でない銘柄の中で付けます。欠損のしかたが変数によって違う場合、組ごとに順位を付け直す順位相関とはわずかに異なります。
- 対象のシグナルが1つで、エクスポージャーも加わらない場合、`pairs` は空になります。

**config の例**

シグナル同士の相関だけを見る場合（example1 でそのまま動きます）:

```yaml
  - name: factor_correlation
    params: {method: pearson, include_exposures: false}
```

リスクモデルのスタイルファクターとの相関も見る場合。config の `risk_models` に、エクスポージャーを持つモデルを定義しておく必要があります（書き方は [03 の 7 章](03_config.md#7-risk_models)）。次の `jpe4` は、config の `risk_models` に定義したモデルの名前の例です。リスクモデルを定義していない config（example1 の `config.example.yaml` など）に書くと、`unknown risk_model 'jpe4'` のエラーになります。

```yaml
  - name: factor_correlation
    params: {risk_model: jpe4, factor_group: style}
```

## 4. 用語

シグナル、metric、カレンダー、日付キー、ホライズン、フォワードリターン、リターン系列、DataBundle など、マニュアル全体で使う用語は [01 の「この文書で使う用語」](01_design.md#この文書で使う用語) を参照してください。ここには、この文書で特に使う用語だけを挙げます。

| 用語 | 意味 |
|---|---|
| キー | metric の1回の実行を区別する名前。config の `id`、なければ `name`（[1.1](#11-config-での指定方法)）。出力のディレクトリ名と `summary.csv` の `metric` 列になる |
| カレンダー行 | カレンダーの行のうち、評価期間（config の `period`）に含まれるもの。「評価期間のすべてのカレンダー行」と書いた表は、データのない日付も欠損の行として含む |
| クロスセクション | ある1つの日付における、銘柄方向のデータの並び |
| IC | Information Coefficient。ある日付の、シグナルとフォワードリターンのクロスセクション相関 |
| 分位 | 日付ごとにシグナルの順位で銘柄をほぼ同数のグループに分けたもの。Q1 がシグナル最小、Q{n} が最大 |
| spread | 最上位の分位と最下位の分位の平均リターンの差（Q{n} − Q1） |
| cumprod / cumsum | リターン系列の `aggregation` に指定する、複数期間のリターンの束ね方。cumprod は複利、cumsum は単純加算（[1.3](#13-リターンの集計規約-aggregation)） |
| 算術平均 / 幾何平均 | 期間リターンの単純平均 / 複利で均した平均（[1.3](#13-リターンの集計規約-aggregation)） |
| Newey-West t 値 | 時系列の系列相関を考慮した標準誤差で求めた t 値（[1.4](#14-t-値の計算方法)） |
| hit_rate | 値が正だった期間の割合 |
| ACF / PACF | 自己相関 / 偏自己相関。[`autocorr`](#autocorr) では、シグナルの銘柄の並びが k 行前とどれだけ似ているかを表す |
| エクスポージャー | リスクモデルが銘柄ごとに持つ、各ファクターへの感応度 |
| カテゴリ | 分類データ（config の `data.classification`）が銘柄に付ける区分。業種など |
