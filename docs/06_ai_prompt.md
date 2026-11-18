# AI チャットに metric のコードを書かせるためのプロンプト

新しい metric を AI チャットツール（ChatGPT、Claude、Copilot Chat など）に書かせるときに、そのまま貼って使えるプロンプトです。AI はこのリポジトリのコードを見られないので、metric を書くのに必要な契約（基底クラス、データの取り出し方、守るルール、実例）をプロンプトの中にすべて入れてあります。

手で書く場合の手順と契約の詳しい説明は [05 metric の追加方法](05_adding_metrics.md) を参照してください。マニュアル全体の目次は [README](README.md) です。

## 使い方

1. 下の「プロンプト」のコードブロックを丸ごとコピーします。
2. 冒頭の「依頼内容」だけを書き換えます。書き方は次の節を参照してください。
3. AI チャットに貼ります。AI の返信は次の3通りのどれかになります。
   - **質問だけが返る**: 決め方で計算結果が変わる点があるときです。各質問に「回答がなければこうする」という既定の案が付いているので、答えるか「既定の案で進めて」と返します。
   - **コードが返る**: metric 本体とテストの2ファイル、config の例、置いた前提の一覧です。
   - **コードを書かないという連絡が返る**: 必要なデータがこのモジュールにない場合と、既存の metric の設定で実現できる場合です。
4. 返ってきた2つのファイルを置きます。
   - metric 本体: `src/alpha_signal_evaluation/metrics/<name>.py`
   - テスト: `tests/metrics/test_<name>.py`
5. 「受け取ったコードの確認」の手順で確かめます。

社外の AI ツールを使う場合、依頼内容には社内のデータやホスト名を書かないでください。プロンプト本体に含まれているのは、このリポジトリで公開している内容だけです。

## 依頼内容の書き方

計算方法を自分の言葉で具体的に書くほど、質問の往復が減り、意図どおりのコードが返ってきます。特に次の点は、決め方によって結果の数字が変わります。

- 日付ごとに何を計算し、それを期間全体でどう要約するか
- 対象にする銘柄（その日付でシグナルとリターンの両方がある銘柄か、リターンのある全銘柄か）
- 銘柄の選び方（上位何 %、端数の丸め方、同順位の扱い、最小銘柄数）
- ウェイト（等ウェイトか、シグナル比例か、ベンチマークウェイトか）
- 出力したい表と、その列

記入例:

```text
- metric の名前: top_hit_rate
- 測りたいこと: シグナル上位の銘柄が、どれだけの割合でユニバースの中央値を上回ったか
- 計算方法: 日付ごとに、シグナルとフォワードリターンの両方がある銘柄を母集団とする（n 銘柄）。
  n が min_obs（既定 10）未満の日付は欠損にする。シグナルの大きい順に ceil(n × top_pct) 銘柄
  （既定 20%。境界で同順位の銘柄はすべて含める）を上位とする。上位のうち、フォワードリターンが
  母集団の中央値を上回った銘柄の割合を求める（日次の的中率、等ウェイト）。期間全体では的中率の
  平均と、「的中率 − 0.5」の平均がゼロかどうかの t 値を出す。
- 使うデータ: シグナルとフォワードリターン
- パラメータ: top_pct（0〜1、既定 0.2）、min_obs（既定 10）
- 出力したい表: 日付ごとの的中率の時系列と、signal × returns × horizon ごとの要約
```

## プロンプト

````text
あなたには、株式アルファシグナルの評価ライブラリ alpha_signal_evaluation に追加する新しい評価指標
（以下 metric）を1つ実装してもらいます。あなたはこのライブラリのコードを見られません。必要な仕様は
すべてこのメッセージに書いてあります。ここに書かれていないライブラリの関数・属性・引数は存在しない
ものとして扱い、推測で使わないでください。足りない処理は pandas / numpy / scipy で自分で書いてください。

# 依頼内容

- metric の名前（config で使う。英小文字とアンダースコア）:
- 測りたいこと:
- 計算方法:
- 使うデータ:
- パラメータ（名前と既定値）:
- 出力したい表:

# 進め方

最初に、次の順で判断してください。

1. コードを書かない場合。次のどちらかに当てはまるときは、コードを書かずに「出力してほしいもの」の
   「コードを書かない場合」の形で答えてください（質問よりもこの連絡を優先します）。
   - 下の「使えるデータ」にないデータが必要（売買代金、出来高、時価総額、株価、日次の系列など）。
     別のデータ（エクスポージャーやベンチマークウェイトなど）で代用したコードを勝手に書いては
     いけません。代用は案として挙げるだけにします。
   - 既存の metric とそのパラメータの組み合わせで実現できる（下の「既存の metric」を参照）。
     依頼の解釈によって実現できるかどうかが変わる場合も、最も自然な解釈で実現できるならこの連絡に
     します。そのとき、新しい metric が必要になる別の解釈を挙げて、どれに当たるかを尋ねてください。
2. 質問する場合。決め方によって計算結果が変わり、かつ下の「既定の決め方」でも決まらない点が
   あるときは、質問だけを返して回答を待ってください（3つまで。各質問に、回答がない場合に採る
   既定の案を添える）。その返信ではコードを書きません。「既定の案で進めて」と言われたら、既定の案で
   実装します。依頼内容に「お任せ」と書かれた項目は指定がないものとして扱い、計算結果が変わる点
   （銘柄の選び方、ウェイトなど）は既定の案を添えて質問します（`min_obs` のような下限は質問にせず
   前提に書きます。ルール 7）。
3. それ以外は、妥当な前提を置いて実装し、置いた前提を最後に列挙してください。

既定の決め方（依頼内容に指定がなければこう決め、質問にはせず、前提に書く）:

- 日付ごとの順位・分位・中央値・ウェイトを決める母集団は、その日付の `data.aligned(signal, returns,
  horizon)` の行（シグナルとフォワードリターンの両方がある銘柄）。リターンを使わない metric では、
  その日付でシグナルのある銘柄。
- 同順位は平均順位（`rank(method="average")`）。分位は下の `assign_quantiles` に従う。
- ホライズン h > 1 の平均は h 期間リターンのまま求め、1期間あたりに換算しない。
- 分母が 0 になるなど、その日付の値を定義できない場合（全銘柄が同順位、ウェイトの絶対値の合計が 0、
  分散が 0 など）は、その日付を欠損にし、`n` は残す。
- 依頼内容の計算方法と下の「守るルール」が食い違うときは、ルールに従って欠損にし、どの行が欠損に
  なるかを前提の先頭に書く。ルール 1（時点合わせ）は依頼内容より常に優先する。

# metric とは

metric は「時点合わせ済みのデータ（DataBundle）を受け取り、結果（MetricResult）を返すクラス」です。
`src/alpha_signal_evaluation/metrics/<name>.py` に1ファイル置くと自動で読み込まれ、`@register_metric`
を付けたクラスが登録されます（同じファイルにモジュールレベルの補助関数や定数を置いて構いません）。
データの読み込み、時点合わせ、config の検証、並列実行、ファイルへの出力（csv / parquet / xlsx、
日付ごとの断面）はライブラリ側が行います。metric はデータの結合と集計だけを行います。

config.yaml では次のように書いて使います。

```yaml
metrics:
  - name: <name>      # 必須
    id: <別名>        # 結果のキーと出力フォルダ名。省略時は name。同じ metric を複数回使うときは必須
    params: {...}     # 省略するとすべて既定値。キーはパラメータのフィールド名
```

既存の metric（この名前は使えません。依頼がこれらの設定で実現できるなら、コードを書かずに設定例を
答えます）。params の signals / returns / horizons（None なら全部）は、書いてある metric だけが持ちます。

- ic: 日付ごとの IC（シグナルとフォワードリターンのクロスセクション相関）。
  params: signals, returns, horizons, method（"spearman" / "pearson"、既定 "spearman"）、min_obs（既定 10）。
  表: timeseries [date, ic, n, signal, returns, horizon]、summary [signal, returns, horizon, mean_ic,
  std_ic, t_stat, hit_rate, n_periods]。
- quantile: 等ウェイトの分位ポートフォリオと、上位 − 下位（Q{n} − Q1）のスプレッド。上位・下位 x% の
  ロングショートは n_quantiles で表せる（10% なら 10）。
  params: signals, returns, horizons, n_quantiles（既定 5）、demean（既定 false。日付ごとの母集団平均を
  引いた超過リターンで集計）、aggregation。表: returns [date, quantile, mean_return, n, signal, returns,
  horizon]、cumulative [date, portfolio, cumulative_return, signal, returns]（h = 1 のみ。portfolio は
  Q1..Q{n} と spread）、summary [signal, returns, horizon, portfolio, arith_mean, geo_mean, t_stat,
  hit_rate]（t_stat と hit_rate は spread の行だけ）。日付ごとのスプレッドの列はなく、returns の
  Q{n} − Q1 で求める。
- category_ic: 分類の列の値ごとの IC。params: signals, returns, horizons, method, min_obs（既定 5）、
  column（使う分類の列。既定 "category"）。表: ic_timeseries [date, category, ic, n, ...]、
  summary [..., category, mean_ic, t_stat, n_periods, avg_n_assets]、mean_signal（カテゴリ別の平均シグナル）。
- category_quantile: カテゴリ内の分位ポートフォリオと、カテゴリ中立のスプレッド。params: signals,
  returns, horizons, n_quantiles（既定 5）、column、aggregation。
- category_returns: カテゴリ別の平均フォワードリターン（シグナルのある銘柄）。params: returns, horizons,
  column、aggregation（signals は持たない）。
- alpha_decay: ホライズン別の IC と分位スプレッド。params: signals, returns, horizons, method,
  n_quantiles（既定 5）、min_obs（既定 10）。表: decay [signal, returns, horizon, mean_ic, ic_t_stat,
  mean_spread, spread_t_stat, spread_per_period]。
- autocorr: シグナルの自己相関と偏自己相関。params: signals, lags（既定 [1, 2, 3, 6, 12]）、method、
  min_obs（既定 10）。
- factor_correlation: シグナルどうし、およびエクスポージャーとの相関の期間平均。params: signals, method,
  include_exposures（既定 true）、risk_model、factor_group（既定 "style"）、min_obs（既定 10）。

# 時点合わせの前提（最も重要）

- 時間軸は「カレンダー」で、1行が1評価期間です（月次なら1か月）。日付は "20240531" のような
  文字列のキーで、日付型には変換しません。
- ホライズン h の単位はカレンダーの行数です（月次カレンダーで h=3 なら3か月）。
- フォワードリターンの行 t の値は「行 t のシグナルを見た後、h 行先までに得られるリターン」です。
  ライブラリが既にシグナルと同じ行に揃えています。したがって、metric の中でリターンやシグナルを
  shift して合わせ直してはいけません（先読みや、他の metric との不整合の原因になります）。
- シグナルとリターンの組は必ず `data.aligned(signal, returns, horizon)` で取り出します。
- シグナル以外の断面データ（分類、ベンチマークウェイト、エクスポージャー、スペシフィックリスク）も、
  行 t の値は行 t の時点で使える値にライブラリが揃えています。シグナルと同じ (date, asset_id) で
  内部結合するだけでよく、shift しません。
- 例外は、シグナルだけを時系列方向に比べる計算（自己相関、銘柄の入れ替わりなど）です。この場合は
  `data.signal_panel(signal)` を行方向に shift して構いません（ラグの単位はカレンダーの行数）。
- カレンダーより細かい頻度のデータは metric には渡されません。「過去 N 日の平均」のような窓の計算や、
  シグナル以外のデータを行方向に shift / rolling する計算が必要な依頼は、時点合わせの変更にあたるので、
  コードを書かずにそのことを伝えてください。

# 使えるデータ（DataBundle）

`compute(self, data)` の `data` が DataBundle です。使えるのは次の属性とメソッドだけです。index の
レベル名は表に書いたとおりの文字列です（"date"、"asset_id"、"factor"）。

| 取り出し方 | 中身 |
|---|---|
| `data.aligned(signal, returns, horizon)` | 同じ (date, asset_id) のシグナルとフォワードリターン。index=(date, asset_id)、columns=[<シグナル名>, "forward_return"]。どちらかが欠損の行は除かれている。データのない日付は行がない。該当する行が1つもなければ、同じ列と index 名を持つ空の DataFrame（`groupby(level="date")` はそのまま空の結果を返すので、空の場合の分岐は不要。最後に `data.dates` で reindex すれば全行が欠損になる） |
| `data.signal_panel(signal)` | シグナルの wide 形式。index=date（`data.dates` の全行）、columns=asset_id。値のない所は欠損 |
| `data.signals` | 全シグナル。index=(date, asset_id)（一意）、columns=シグナル名。ユニバース適用済み。あるシグナルだけが欠損の行があるので、1つのシグナルの有無は行の有無ではなく `data.signals[signal].notna()` で判定する |
| `data.dates` | 評価対象の全カレンダー行の日付キー（昇順の tuple[str, ...]）。シグナルのない行も含む |
| `data.resolve_signals(p.signals)` | パラメータが None なら全シグナル名、指定があればそのリスト |
| `data.resolve_returns(p.returns)` | 同上（リターン系列名。例: ["total", "specific"]） |
| `data.resolve_horizons(p.horizons)` | 同上（ホライズン。昇順の list[int]） |
| `data.return_kinds[returns]` | そのリターン系列の種類。"total" または "specific" |
| `data.forward_returns[returns][horizon]` | フォワードリターンの wide 形式。index=date、columns=asset_id（通常は aligned を使う） |
| `data.classification` | 銘柄の分類。index=(date, asset_id)、columns=["category", ...]（config の分類ソースにある他の列も、元の列名のまま入る）。設定がなければ None |
| `data.benchmark_weights` | ベンチマークウェイト。index=(date, asset_id)（一意。日付は `data.dates` の範囲内）、columns=["weight"]。ソースの値のままで、ユニバースでは絞られない（シグナルのない銘柄の行も含む）。日付ごとの合計が 1 とは限らず、0・欠損・負の値も検証されずに入りうる。設定がなければ None |
| `data.risk_model(name)` | `(リスクモデル名, RiskModelData)` を返す。name が None のときは、リスクモデルが1つだけならそれを返す（0個または複数なら ValueError） |

RiskModelData の属性（どれも、設定されていなければ None または空）:

| 属性 | 中身 |
|---|---|
| `exposures` | ファクターエクスポージャー。index=(date, asset_id)、columns=ファクター名。シグナルのない銘柄の行を含むことがあり、ファクター単位で欠損することもある |
| `factor_covariance` | ファクター共分散（年率の小数²）。index=(date, factor)、columns=ファクター名。日付ごとの正方行列を縦に積んだ形 |
| `specific_risk` | スペシフィックリスク。index=(date, asset_id) |
| `forward_factor_returns[horizon]` | フォワードファクターリターン。index=date、columns=ファクター名。フォワードリターンと同じ規約で時点合わせ済み |
| `specific_return` | 対応するスペシフィックリターンの系列名（str または None） |
| `factor_groups` | グループ名 → ファクター名のリスト（dict[str, list[str]]）。未設定なら None |
| `factors(group=None)` | ファクター名のリスト（exposures の列順）。group（例: "style"）を渡すとそのグループに絞る。group が factor_groups にない場合と factor_groups が未設定の場合は、エラーにせず全ファクターを返す |

ファクター名は config ごとに違います。特定のファクター名をコードや既定値に決め打ちせず、
`model.factors(group)` で取得するか、パラメータで受け取ってください。

リターンの種類（kind）は "total" と "specific" の2つです。期間をまたいで束ねるとき、total は複利、
specific は加算です。specific を「residual」とは呼びません。

データの種類は metric の側からは増やせません。新しい種類のデータは、ライブラリの読み込み側への
追加が必要です。そうしたデータが必要な依頼では、コードを書かずに、必要なデータの形を伝えてください。

# クラスの書き方

```python
import numpy as np
import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import ASSET_ID, CATEGORY, DATE, FACTOR, WEIGHT  # 各定数は小文字の同名の文字列
from .base import Metric, MetricParams, MetricResult, ReturnAggregation, SignalReturnParams
from .registry import register_metric
```

`"forward_return"` には定数がないので、文字列で直接書きます。

パラメータ（pydantic のモデル。config.yaml の params がここで検証される）:

- シグナル × リターン × ホライズンの組ごとに計算する metric は `SignalReturnParams` を継承します。
  `signals` / `returns` / `horizons`（いずれも `list | None = None`。None は「全部」）を持っています。
- リターンを使わない metric は `MetricParams` を継承します。シグナルごとに計算する metric には、依頼に
  パラメータがなくても `signals: list[str] | None = None` を持たせます（対象のシグナルを絞る共通の
  フィールドで、計算方法のパラメータには数えません）。
- 追加するフィールドには必ず既定値を付けます（params を省略しても動くように）。値の範囲は
  `Field(default=10, ge=3)` のように宣言します。未知のキーは自動で拒否されます。
- リスクモデルを使う場合は `risk_model: str | None = None` を定義し、`data.risk_model(p.risk_model)` で
  引きます。
- `signals` / `returns` / `horizons` / `risk_model` という名前のフィールドは、config に実在するかが
  計算前に自動で検証されます。

metric クラス:

- `name`: config で参照する名前（上の既存の metric と重複不可）
- `description`: 一覧表示用の1行の説明（英語）
- `requires`: 使うデータの宣言（frozenset）。書ける名前は "signals"、"forward_returns"、
  "classification"、"benchmark_weights"、"risk_models"（リスクモデル全体）、
  "risk_models.exposures"、"risk_models.factor_covariance"、"risk_models.specific_risk"、
  "risk_models.factor_returns"。これ以外の名前は使えません（未知の名前はクラス定義の時点で
  TypeError。予約済みの "portfolio_weights" はライブラリが値を入れないので使いません）。
  宣言していないデータは、テストでは空（None や空の辞書）にして渡されるので、使うものは必ず宣言
  します。宣言したデータが config にない場合はライブラリが計算前にエラーにするので、compute の中で
  None かどうかを確かめる必要はありません。`factors()` と `factor_groups` は
  "risk_models.exposures" を宣言すれば使えます。
- `Params`: 上のパラメータのクラス
- `compute(self, data: DataBundle) -> MetricResult`: 計算本体。パラメータは `self.params`。
  計算を続けられない場合は ValueError を出して構いません（ライブラリが metric 単位で記録し、他の
  metric は続行します）。空の表を返してごまかさないでください。
- 独自の事前検証が必要なら `check` を上書きします:
  ```python
  @classmethod
  def check(cls, params, spec) -> list[str]:
      errors = super().check(params, spec)
      # spec.signals / spec.return_kinds / spec.horizons で名前の実在を確認できる。
      # spec.risk_models は「リスクモデル名 → 揃っている構成要素名の集合」（例: {"exposures"}）
      if ...:
          errors.append("エラーメッセージ（英語）")
      return errors
  ```
  ファクター名やファクターのグループは spec に入っていないので、`factor_group` のようなパラメータは
  check では検証できません。compute の中で `model.factor_groups` を見て、存在しなければ ValueError を
  出してください。

結果（MetricResult）:

- `tables: dict[str, pd.DataFrame]`: 表の名前 → DataFrame。long 形式で、index は RangeIndex にします
  （`reset_index()` する）。キーの列名は `signal` / `returns` / `horizon`、ファクターごとの行なら
  `factor` に揃えます。表の構成はパラメータで変えません（該当がなければ空の DataFrame を入れる）。
- 日付ごとの値を持つ表には `date` 列を持たせます（ライブラリが日付ごとの断面として自動で切り出します）。
  - 1つの組（signal × returns × horizon など）につき1行が1日付の表は `tables["timeseries"]` とし、
    列は [date, <値>, n, signal, returns, horizon] の順にします。値の列名は測る量の名前にします（IC は
    `ic`、ウェイト付きポートフォリオのリターンは `portfolio_return`、等ウェイト平均は `mean_return`）。
    `data.dates` の全行で reindex し、値を定義できない日付（その metric の母集団が空の日付）を欠損の
    行として残します。母集団はあるが該当する銘柄が 0 件の日付は、欠損ではなく定義どおりの値（割合なら
    0）にします。
  - 日付のほかに軸（グループ、カテゴリ、分位、ファクター）を持つ表は、データのある (日付, グループ) の
    組だけを行にし、全組み合わせには広げません。
  - 下限未満などで値を欠損にした行でも、銘柄数の列（`n`）は残します。`n` は母集団（分母）の銘柄数で、
    該当銘柄数など別の件数は `n_<内容>`（例: `n_covered`）の列にします。reindex で足した行の `n` は
    欠損のままで構いません。
- リターンの時系列を要約する表の列は [キーの列..., arith_mean, geo_mean, t_stat, hit_rate, n_periods]
  とし、summary の dict には arith_mean / geo_mean / t_stat を入れます。相関などリターン以外の量は
  `<統計量>_<量>` の形（`mean_ic`、`std_ic`、`min_coverage` など）にし、summary には `mean_<量>` と、
  意味があれば `t_stat` を入れます。t 値や hit_rate は、依頼にあるか 0 との比較に意味がある量のときだけ
  出します。
- 累積リターンの表は `tables["cumulative"]` とし、columns=[date, (portfolio,) cumulative_return,
  signal, returns] にします（h = 1 だけなので horizon 列は持たせません）。h = 1 を計算しない場合も
  表は省略せず、空の DataFrame を入れます（`concat([])` の戻り値のままでよく、列は不要です）。
- `summary: dict[str, float]`: 1つの数値で表せる要約。値は必ず float です（計算できない場合は NaN を
  入れ、キーは省かない）。キーは `/` 区切りで、signal × returns × horizon の metric は
  `"{signal}/{returns}/h{horizon}/{stat}"`、リターンやホライズンを持たない metric は持っている次元だけを
  つなぎます（例: `"{signal}/{stat}"`、`"{signal}/lag{lag}/{stat}"`）。`flatten_summary` で作れますが、
  `h` 以外の接頭辞（`lag2` の `lag` など）は付かないので、必要なら列の値を "lag2" のような文字列に
  してから渡すか、dict 内包表記で自分で作ります。
  グループが少数で固定（large / small など）なら、グループの列を最後の次元として足します
  （`"alpha/total/h1/large/mean_ic"`）。カテゴリやファクターのように数が config 次第のものは summary に
  入れず、表だけで返します（summary は空の dict で構いません）。
- `notes: list[str]`: 計算方法の補足（英語の短い文）。
- `tables` か `summary` の少なくとも一方は空でないこと。

# 共通ヘルパー

`from ._common import ...` で使えます。ここにないものはありません。

| 関数 | 内容 |
|---|---|
| `cross_sectional_corr(df, x, y, method, min_obs, by=None)` | 日付（と by）ごとに df の列 x と y の相関。method は "spearman" / "pearson"。by は df の列名のリスト（例: `by=["category"]`）で、by の列が欠損の行はどのグループにも入らない。戻り値は index=(date, *by)、columns=["corr", "n"]。データのある組だけが行になる。観測数が min_obs 未満の組は行が残り、corr だけが欠損（n は入る）。空の df なら空の表 |
| `summarize_series(x, horizon=1)` | 時系列（index=date の Series）の要約。{"mean", "std", "t_stat", "hit_rate", "n_periods"} の dict（値はすべて float）。欠損は除いて計算するので、欠損を含む Series をそのまま渡してよい。std は ddof=1、hit_rate は値が正の期間の割合、n_periods は欠損でない期間数、t_stat は lag = horizon − 1 の Newey-West（下の newey_west_tstat。std から計算した値ではない）。全て欠損でも例外は出ず、n_periods は 0.0、他は NaN |
| `newey_west_tstat(x, lags)` | 平均がゼロかどうかの Newey-West t 値（float）。欠損は除く。有効な値が 2 未満なら NaN。分散は n で割る（ddof=0）ので、lags=0 のときは `mean / (std(ddof=0) / sqrt(n))` に一致し、通常の t 値（ddof=1）の sqrt(n / (n − 1)) 倍になる。標準誤差が 0（値がすべて同じ）なら NaN。テストで検算するときはこの式で計算する |
| `assign_quantiles(values, n_quantiles, by=None)` | 日付（と by）ごとに 1（最小）〜 n_quantiles（最大）の分位を割り当てる。values は index=(date, asset_id) の Series。by は values と同じ index の Series。分位 = floor((rank − 1) × n_quantiles / n) + 1（rank は日付内の昇順の順位で、同順位は出現順に分ける。n はその日付の欠損でない値の数）。値の境界で切る qcut ではなく、各分位の銘柄数がほぼ均等になる（最上位分位は floor(n / n_quantiles) 銘柄）。戻り値は values と同じ index の float の Series。values が欠損の行は欠損、n < n_quantiles の日付はすべて欠損 |
| `quantile_portfolios(df, quantile, n_quantiles, dates)` | 分位ごとの等ウェイト平均リターン。df は aligned の出力、dates は `pd.Index(data.dates, name=DATE)`。戻り値は (long, wide)。long の columns=[date, quantile, mean_return, n]（データのある組だけ）、wide は index=dates、columns=[Q1, ..., Q{n}, spread]（spread = Q{n} − Q1） |
| `summarize_portfolios(wide, horizon, aggregation)` | wide（index=date、列名は任意）の各列の要約。dict のリスト（portfolio, arith_mean, geo_mean。列名が "spread" の列だけ t_stat, hit_rate も） |
| `cumulative_portfolios(wide, kind, aggregation)` | wide（index の name が "date"、列名は任意）の各列の累積リターン（long）。columns=[date, portfolio, cumulative_return] |
| `per_period_rate(r, horizon, method)` | h 期間のリターンを1期間あたりに換算。method は "compound" / "sum"。異なるホライズンどうしを比べるときだけ使う |
| `flatten_summary(frame, keys, stats)` | 要約の表（1行が1組）を dict にする。keys の列の値を渡した順に `/` でつなぎ、最後に `/{stat}` を付ける。`horizon` という名前の列だけ `h{値}` と書く。keys は何個でもよく、horizon がなくてもよい（例: `["signal"]` → `"alpha/mean_turnover"`、`["signal", "returns", "horizon"]` → `"alpha/total/h1/mean_ic"`）。欠損は NaN のまま入る |
| `concat(frames)` | DataFrame のリストを縦に連結（index を振り直す。空リストなら空の DataFrame） |
| `CorrMethod` | `Literal["spearman", "pearson"]` の型 |

ゼロ以外の基準値 c との差を検定するときは `summarize_series(x - c, h)["t_stat"]` を使います
（このとき mean / hit_rate は差の系列に対する値です）。

期間をまたぐリターンの集計には `ReturnAggregation` を使います。パラメータに
`aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)` を持たせ、次を呼びます。
フィールドは `cumulative`（kind → "compound" / "sum"。既定は total が compound、specific が sum）だけです。

- `p.aggregation.summarize(series)` → {"arith_mean": float, "geo_mean": float}。欠損は除いて計算し、
  有効な値がなければ両方 NaN（例外は出ない）。geo_mean は `expm1(mean(log1p(r)))`
  （= (∏(1 + r))^(1/n) − 1）で、r <= −1 の値が1つでもあれば NaN。kind によらず同じ式で、specific の
  系列でも両方を出す。ホライズンの換算はしない。
- `p.aggregation.cumulate(series_or_frame, kind)` → 入力と同じ index・columns の累積リターン。
  "compound" は `(1 + r).cumprod() - 1`、"sum" は `r.cumsum()`（どちらも 0 始まりのリターンで、1 始まりの
  指数ではない）。欠損の日付は欠損のまま残り、累積はその前後をつないで続く。Series を渡せば Series が返る。

# 守るルール

1. 時点をずらさない（上の「時点合わせの前提」のとおり）。
2. 欠損を 0 で埋めない。`fillna(0)` は使いません。リターンやウェイトなど値の合計は
   `sum(min_count=1)`、ローリングは `min_periods=窓の長さ` にし、データが足りない所は欠損のまま
   返します（真偽値の件数を数える `sum()` は対象外です）。割合の分子のように「該当する銘柄が 0 件なら
   0」が正しい合計は、母集団が空でない日付に限って 0 にします（母集団の行に 0 / 1 の指示変数を掛けて
   から合計する）。これは欠損の 0 埋めにはあたりません。
3. データを読みに行かない、ファイルに書かない、print しない。結果は MetricResult で返すだけです。
4. `requires` に宣言したデータだけを使う。
5. インスタンスに状態を持たない（`self.params` 以外の属性を足さない。並列実行で pickle されます）。
6. ホライズン h > 1 のリターンは隣の行と期間が重なります。t 値は Newey-West（`summarize_series` か
   `newey_west_tstat(x, h - 1)`）で求め、累積リターンは h = 1 のときだけ作ります。リターンを使わない
   時系列（エクスポージャーなど）で t 値を出す場合は、求め方の指定がなければ `summarize_series(x)` を
   使い、自己相関を補正していないことを notes に書きます。
7. 銘柄数が少なすぎて計算できない日付は欠損にする。下限のパラメータ（`min_obs` など）が依頼内容に
   あればそれを使います。なければ、ヘルパーの下限（`assign_quantiles` なら n_quantiles）に任せるか、
   相関などを自分で計算する場合は `min_obs`（既定 10）を追加して構いません（追加したことは前提に
   書き、質問にはしない）。`min_obs` の下限（`ge`）は、計算が定義できる最小の銘柄数にします（相関は 3、
   順位や分位からウェイトを作る場合は 2）。
8. デフォルトのパラメータで、小さなデータ（36日付 × 80銘柄）に対して例外を出さずに動くこと。
9. docstring とコメントは日本語で書く。クラスの docstring には、何をどう測るか、出力する表の名前と列、
   summary のキーを書く。型エイリアスは `TypeAlias` で書く。Python 3.12。1行は表示幅で100桁まで
   （全角文字は2桁に数える）。数え間違いに備えて 96 桁以内を目安にし、日本語だけの行は約45文字で
   折り返す。docstring の `columns=[...]` は日本語の文に続けず、新しい行から書く。コードの文は100桁に
   収まるなら1行に書く。
10. ICIR は出力しない。

# 実例1（組み込みの ic metric。docstring を短くしたもの）

構成（パラメータのクラス、クラス属性、3重ループ、表の作り方、summary の作り方）はこの例に合わせて
ください。

```python
"""IC（Information Coefficient）の metric ``ic``。

シグナルと同じ行のフォワードリターンとのクロスセクション相関を日付ごとに求め、
その時系列を要約する。
"""

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import CorrMethod, concat, cross_sectional_corr, flatten_summary, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class ICParams(SignalReturnParams):
    """``ic`` metric のパラメータ。

    Attributes:
        method: 相関の種類。``"spearman"``（順位相関、デフォルト）または ``"pearson"``。
        min_obs: 日付ごとに IC を計算する最小銘柄数（3 以上）。デフォルト 10。
            未満の日付の IC は欠損。
    """

    method: CorrMethod = "spearman"
    min_obs: int = Field(default=10, ge=3)


@register_metric
class InformationCoefficient(Metric):
    """IC（シグナルとフォワードリターンのクロスセクション相関）。

    シグナル × リターン系列 × ホライズンの組ごとに、日付ごとのクロスセクション相関を求める。
    銘柄数が ``min_obs`` 未満の日付は欠損になる。IC の時系列から平均・標準偏差・t 値・
    正の割合・期間数を求める。t 値は lag = horizon − 1 の Newey-West。

    出力:
        tables["timeseries"]: 日付ごとの IC（評価対象の全カレンダー行。データのない日付は欠損）。
            columns=[date, ic, n, signal, returns, horizon]。
        tables["summary"]: 1行が (signal, returns, horizon) の1組。
            columns=[signal, returns, horizon, mean_ic, std_ic, t_stat, hit_rate, n_periods]。
        summary: ``"{signal}/{returns}/h{horizon}/mean_ic"`` と ``".../t_stat"``。
    """

    name = "ic"
    description = "IC (spearman or pearson)"
    requires = frozenset({"signals", "forward_returns"})
    Params = ICParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組について IC の時系列と要約を求める。"""
        p = self.params
        series, summary = [], []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h)
                    ic = cross_sectional_corr(df, signal, "forward_return", p.method, p.min_obs)
                    ic = ic.reindex(pd.Index(data.dates, name=DATE))
                    key = {"signal": signal, "returns": ret, "horizon": h}
                    series.append(ic.rename(columns={"corr": "ic"}).assign(**key).reset_index())
                    summary.append({**key, **summarize_series(ic["corr"], h)})

        summary = pd.DataFrame(summary).rename(columns={"mean": "mean_ic", "std": "std_ic"})
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary=flatten_summary(
                summary, ["signal", "returns", "horizon"], ["mean_ic", "t_stat"]
            ),
            notes=["t_stat is Newey-West with lag = horizon - 1 (overlapping horizons)."],
        )
```

# 実例2（リターンを使わない metric の骨格）

シグナルだけを使う metric は、パラメータが `MetricParams` を継承し、`requires` が "signals" だけに
なり、summary のキーからリターンとホライズンがなくなります。

```python
class MySignalParams(MetricParams):
    """パラメータ（リターンを使わないので MetricParams を継承する）。"""

    signals: list[str] | None = None
    lag: int = Field(default=1, ge=1)


@register_metric
class MySignalMetric(Metric):
    name = "my_signal_metric"
    description = "..."
    requires = frozenset({"signals"})
    Params = MySignalParams

    def compute(self, data: DataBundle) -> MetricResult:
        p = self.params
        series, summary = [], []
        for signal in data.resolve_signals(p.signals):
            panel = data.signal_panel(signal)  # index=date（全カレンダー行）、columns=asset_id
            previous = panel.shift(p.lag)  # シグナルどうしの比較なので行方向に shift してよい
            value = ...  # index=date の Series（panel と previous から日付ごとに計算する）
            series.append(value.rename("value").reset_index().assign(signal=signal))
            summary.append({"signal": signal, "mean_value": float(value.mean())})
        summary = pd.DataFrame(summary)
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary=flatten_summary(summary, ["signal"], ["mean_value"]),  # "alpha/mean_value"
        )
```

分類やリスクモデルを使う場合の結合の仕方:

```python
# 分類: シグナル・リターンと (date, asset_id) で内部結合する
df = data.aligned(signal, ret, h).join(data.classification[["category"]], how="inner")
df = df.dropna(subset=["category"])

# リスクモデル: エクスポージャーを (date, asset_id) で内部結合する
_, model = data.risk_model(p.risk_model)
factors = model.factors()  # ファクター名のリスト
df = data.signals[[signal]].join(model.exposures[factors], how="inner").dropna(subset=[signal])
```

# テスト

テストには合成データの fixture `bundle` が使えます（`tests/conftest.py` にあり、引数に `bundle` と
書くだけで渡されます。全テストで共有されるので変更しないでください）。中身は次のとおりです。

- 36日付（"20150131" から始まる月末）× 80銘柄（"A0000" 〜 "A0079"）
- シグナル: `alpha`、`noise`、`mixed`（alpha と noise の平均）。約 5% がランダムに欠損し、日付ごとの
  有効な銘柄数は 70〜80。値は連続で同順位はない。3つとも行ごとに独立な乱数で、時系列方向の持続性は
  ない（自己相関はほぼ 0）
- リターン系列: `total`（kind=total）と `specific`（kind=specific）。ホライズンは 1 と 3。
  フォワードリターンは36日付すべてにあり、欠損はない
- `alpha` は値が大きいほど次の行の specific リターンが高い（平均 IC は h=1 で約 0.3、h=3 で約 0.2。
  total でもほぼ同じ）。`noise` の IC はほぼ 0
- 分類: `category` 列に "S0" 〜 "S4"（銘柄ごとに固定）
- リスクモデル "barra": ファクターは size / momentum（グループ style）と ind_a / ind_b
  （グループ industry）。エクスポージャー（欠損なし）、共分散、スペシフィックリスク、ファクター
  リターンを持つ。momentum は alpha と正に相関し、size はどのシグナルとも無関係
- ベンチマークウェイト: 全80銘柄、正の値で、日付ごとに合計 1（上位 25 銘柄前後で累積約 70%）

テストでは metric を次のように作って実行します。範囲外の値や未知のキーを渡すと、作る時点で
`pydantic.ValidationError`（`ValueError` のサブクラス）になるので、`pytest.raises(ValueError)` で
確かめられます。

```python
from alpha_signal_evaluation.metrics import get_metric

result = get_metric("<name>")(signals=["alpha"], horizons=[1]).compute(bundle)
table = result.tables["summary"]
```

境界のケース（データのない日付、銘柄数の少ない日付、同順位）は、fixture を変更せずに別の bundle を
作って確かめます。metric のファイルに置いた補助関数は
`from alpha_signal_evaluation.metrics.<name> import <関数>` で import できます。

```python
import dataclasses

import numpy as np

from alpha_signal_evaluation.testing import make_synthetic_bundle

# ある日付のシグナルをすべて欠損にした複製（dates は元のまま引き継がれる）
signals = bundle.signals.copy()
signals.loc[bundle.dates[3], "alpha"] = np.nan
no_data = dataclasses.replace(bundle, signals=signals)

# 銘柄数の少ない別の bundle（fixture と同じ作り）
tiny = make_synthetic_bundle(n_dates=6, n_assets=8, horizons=(1,), missing_rate=0.0)
```

`make_synthetic_bundle` の引数は `n_dates=36, n_assets=80, horizons=(1, 3), seed=0, missing_rate=0.05` の
5つです。大きさによらず、シグナル名（alpha / noise / mixed）、リターン系列、分類、リスクモデル "barra"、
ベンチマークウェイトは fixture と同じで、フォワードリターンは全日付・全銘柄にあります。DataBundle は
frozen な dataclass なので、`signals` のほか `classification` / `benchmark_weights` / `risk_models` /
`forward_returns` も `dataclasses.replace` で差し替えられます（元の DataFrame は `.copy()` してから
変更する）。`aligned` と `signal_panel` は呼ぶたびに `signals` から作るので、複製にそのまま反映されます。

テストに含めるもの:

- 1つの日付について、metric と共通ヘルパーを使わずに pandas で直接計算した値と一致すること
  （リターンを使う metric は `bundle.aligned("alpha", "specific", 1).xs(date, level="date")` から、
  使わない metric は `bundle.signal_panel("alpha")` や `bundle.risk_model(None)[1].exposures` から
  自分で計算する）
- 合成データの作りから言える大小関係になること（リターンを使う metric なら、予測力のある `alpha` と
  無関係な `noise`。言える関係がなければ省略し、値の範囲など定義から決まる性質を確かめる）
- 下限を満たさない場合や、データのない日付が欠損になること（上の別の bundle を使う）
- `result.summary` の値が表の値と一致すること

# 出力してほしいもの

コードを書く場合は、次の順に出力してください。コード以外の説明は短くしてください。

1. `src/alpha_signal_evaluation/metrics/<name>.py` の全文（1つのコードブロック。省略や「ここに実装」は不可）
2. `tests/metrics/test_<name>.py` の全文（1つのコードブロック）
3. config.yaml に書く例（上の `metrics:` の書式で1エントリ）
4. 置いた前提と、人間に確認してほしい点（箇条書き）

データが足りないためにコードを書かない場合は、代わりに次を出力してください。

1. 足りないデータの名前と、それが必要になる計算の箇所
2. そのデータをライブラリに追加するなら必要な形（index、列、どの時点までの情報か）
3. 今あるデータで近いことをする方法があれば、その案と依頼内容との違い

既存の metric で実現できるためにコードを書かない場合は、代わりに次を出力してください。

1. 同じことができる既存の metric と、config.yaml の1エントリの設定例
2. 依頼の出力が、その metric のどの表・列・summary のキーに当たるか（直接は出ない値は、どの表から
   どう求めるか）
3. 依頼内容との違い（銘柄数の丸め方、同順位、ウェイトなど）と、新しい metric が必要になる条件
````

## 受け取ったコードの確認

AI が契約を誤解したまま、もっともらしいコードを返すことがあります。ファイルを置いたら次を実行してください。

```sh
uv run ruff format src tests && uv run ruff check src tests   # 整形と lint。docstring やコメントの行が長い（E501）場合は自動では直らないので手で折り返す
uv run alpha-eval metrics list -v                 # 登録され、パラメータが意図どおりか
uv run pytest tests/metrics/test_contract.py      # 契約テスト（全 metric に自動で走る）
uv run pytest tests/metrics/test_<name>.py        # AI が書いたテスト
```

テストが通っても、次の点はコードを読んで確かめてください。テストでは検出しにくく、結果の数字だけが静かに変わる誤りです。

| 確認する点 | 見るところ |
|---|---|
| 時点をずらしていないか | `forward_return` やフォワードリターンの表に `shift` を使っていない。シグナルとリターンの組を `data.aligned()` で取り出している |
| 欠損を 0 で埋めていないか | `fillna(0)` がない。リターンなど値の合計に `min_count=1` が付いている（件数を数える `sum()` は除く） |
| 計算方法が依頼どおりか | AI が最後に列挙した「置いた前提」を読む。母集団、同順位の扱い、端数の丸め方、ウェイト、最小銘柄数が意図と合っているか |
| テストが実装をなぞっていないか | 1日付の検算が、metric と同じ関数を呼ばずに pandas で直接計算している |
| テストが空振りしていないか | 「欠損になること」を確かめるテストで、実際に欠損の行が1つ以上あることを assert している |
| `requires` に過不足がないか | 使っているデータがすべて宣言されている（契約テストでも検出されます） |

最後に、config.yaml の `metrics` に追加して実データで実行し、既存の metric（`ic` や `quantile`）の結果と矛盾しないかを見てください。

## このプロンプトの検証

このプロンプトは、リポジトリを見られない AI に6種類の依頼を渡して確かめています（上位銘柄の的中率、上位分位の入れ替わり率、分位スプレッドのファクターエクスポージャー、大型・小型別の IC、順位加重ロングショート、使えないデータを要求する依頼）。コードを書く5種類は、生成されたファイルを実際に登録し、契約テストと評価の実行、独立に計算した値との照合まで通ることを確認しました。使えないデータを要求する依頼は、コードを書かずに断ることを確認しました。プロンプトを変更したら、同じように1〜2種類の依頼で試してください。

## このプロンプトの保守

プロンプトには metric の契約を書き写してあります。次のファイルを変更したら、プロンプトの該当箇所も更新してください。

| 変更したファイル | 更新する箇所 |
|---|---|
| `metrics/base.py` | 「クラスの書き方」 |
| `data/bundle.py` | 「使えるデータ（DataBundle）」 |
| `metrics/_common.py` | 「共通ヘルパー」 |
| `metrics/ic.py` | 「実例1」 |
| `testing.py`、`tests/conftest.py` | 「テスト」の合成データの説明 |
| 組み込み metric の追加・削除 | 「既存の metric」 |
