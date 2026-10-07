# briskapi

[English](README.md) | [日本語](README.ja.md)

BRiSK の板寄せデータを扱う、非公式の pybrisk 風 Python API と `brisk`
コマンドラインツールです。ライブフィードの購読、記録データの任意時点での照会、
公開アーカイブからの共有記録の取得ができます。本プロジェクトは独立したもので、
BRiSK、立花証券、SBI証券、東京証券取引所（TSE）、日本取引所グループ（JPX）とは
提携しておらず、承認も受けていません。

> **現在利用できるデータ:** 2021年9月27日の公開 BRiSK Next デモ（寄り前の
> スナップショット1件と寄付から3分間）を、記録時のペースで再生したものです。
> リアルタイムの市場データではありません。リアルタイムのデータには立花証券の
> 口座が必要で、まだ対応していません。

## インストール

Python 3.12 以上、Node 22 以上、Rust 1.92 以上が必要です。

```sh
git clone https://github.com/honvl/briskapi && cd briskapi
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[pandas]'   # `import brisk` と `brisk` コマンド
cargo build --locked --release --manifest-path rust/brisk_quote_ingest/Cargo.toml   # `brisk record` を使う場合のみ
```

上記のように、クローンしたリポジトリから編集可能モード（`-e`）でインストール
してください。API はリポジトリ内のデコーダーとアーカイブ設定をそのまま使います。
BRiSK のデコーダーとデモデータは実行時にダウンロードされ、リポジトリには含まれて
いません。

## ライブフィード

```python
import brisk

feed = brisk.connect(web=True, codes=["7203", "6758"])   # 初期状態を受信してから戻る
toyota = brisk.Ticker("7203")
toyota.quote()        # 現在の気配（フレームが届くたびに更新）
toyota.auction()      # 予想約定価格・数量と成行注文の売買差

feed.on_quote(lambda q: print(q["code"], q["indicative_price"]), codes="6758")
for q in feed.quotes("7203"):       # 更新ごとに1件。セッションが終わると終了
    if q["last_price"]:
        print("寄付:", q["last_price"], q["time"])
        break

brisk.Market().imbalances(top=10).to_pandas()
feed.wait()           # または feed.close()。`with brisk.connect(...) as feed:` も使えます
```

主なオプション: `web=True`（デモサイトから取得）または `cache=DIR`
（`python tools/brisk_mock/download_mock.py --cache DIR` で事前にダウンロード
したデータ）、`codes`（銘柄コード）、`speed`（`1` で実時間、`0` で最速）、
`history=True`（`Ticker.history()` のために更新を保持）。コールバックと
イテレーターは、まず各銘柄の現在の気配を受け取り、その後すべての更新を順番
どおりに受け取ります。受け取る側の処理が遅い場合、更新を捨てずにフィードの
ほうが待ちます。

## 記録データとアーカイブ

```python
brisk.recordings(source="historical_mock")   # 公開済みの記録一覧（AWS アカウント不要）
brisk.pull("archive/20210927/SHA256")       # ダウンロード・検証・展開・キャッシュし、既定のデータにする
brisk.load("recordings/my-session")         # ローカルの記録（events.jsonl[.gz] またはフォルダー）

brisk.Ticker("7203").quote(at="08:59:59.99")               # 任意の日本時間時点の状態
brisk.Ticker("7203").history(start="09:00", end="09:01")   # 期間内のすべての更新
brisk.Market().snapshot(at="09:00:00").to_pandas()
```

## API リファレンス

| 呼び出し | 戻り値 |
| --- | --- |
| `brisk.connect(...)` | ライブの `Feed`（既定のデータ源になる） |
| `Ticker(code).info()` | 銘柄名、売買単位、呼値の種別、基準値、値幅制限 |
| `Ticker(code).quote(at=None)` | 買い・売り気配、予想約定価格・数量、成行・引け条件付きの数量、直近の約定 |
| `Ticker(code).auction(at=None)` | 板寄せの予想状態と `market_order_imbalance`（成行の買い数量 − 売り数量） |
| `Ticker(code).history(start, end)` | すべての更新（時系列順） |
| `Market().stocks()` | 全銘柄のマスター |
| `Market().snapshot(at=None)` | 全銘柄の気配 |
| `Market().imbalances(at=None, top=None)` | 成行注文の売買差（絶対値）が大きい順の銘柄 |
| `Market().summary()` | データ源、日付、銘柄数、時刻の範囲 |
| `Feed.quotes(codes)` / `Feed.on_quote(fn, codes)` | 届いた順のライブ更新 |
| `brisk.recordings()` / `brisk.pull()` / `brisk.load()` | アーカイブの一覧、検証付きダウンロード、ローカルファイル |
| `brisk.record(output, web=True, ...)` | Rust レコーダーによる記録（共有設定に従って共有） |
| `brisk.consent(...)` | 共有の設定 |

価格は円単位の浮動小数点数で、ベンダーの「値なし」（0）は `None` になります。
時刻は取引日の日本時間の `datetime` です。数量は株数で、売買区分・フラグ・
ステータスはベンダーの値のままです。`raw=True` を指定すると、ベンダー形式
（`*_price10` は0.1円単位、`*_us` は日本時間0時からのマイクロ秒）で返します。
表形式の結果は dict のリストで、`.to_pandas()` で DataFrame に変換できます。
エラーは `brisk.BriskError` と `brisk.NotFoundError` です。市場全体の照会では
記録を1回読み込みます（デモ全体の 420 MB で約6秒）。

## コマンドライン

```sh
brisk live --web --codes 7203,6758          # 気配の更新ごとに JSON を1行出力（--raw でベンダー形式）
brisk record --web --output recordings/s1   # デモを記録（同意済みなら共有）
brisk list --date 20210927 --source historical_mock
brisk pull archive/20210927/SHA256 --output recordings/downloaded
brisk consent [--accept | --revoke]         # 共有設定の表示・変更
brisk upload recordings/s1                  # 記録の共有を再試行
```

各コマンドの詳細は `--help` で確認できます。`pull` はすべて検証してから
書き込み、既存のフォルダーを上書きすることはありません。

## 記録の共有

コマンドラインで初めて記録またはライブセッションを始めると、共有される内容が
表示され、一度だけ確認されます（Enter で同意）。それ以降は、最後まで正常に
終わったセッションが自動的にアップロードされ、公開されます。Python API から
確認を求めることはありません。決めるまでは、セッションはお使いのコンピューター
にだけ保存されます。

- **共有される内容:** 記録した市場データ、ローカルの計測値（お使いの
  コンピューターの時計を含み、記録した日時がわかります）、公開エイリアス
  （既定はランダムな `anon-…`）とライセンス。IP アドレスはアップロード回数の
  制限にのみ使います。
- **公開範囲:** 公開された記録は誰でも閲覧でき、永続的に残り、ご自身では削除
  できません。詳しくは [PRIVACY.md](PRIVACY.md)（英語）をご覧ください。
- **共有をやめる:** `brisk consent --revoke`、環境変数 `BRISK_CONTRIBUTE=0`、
  または1回だけなら `--no-upload`。
- **ライセンス:** 同意すると、選んだデータライセンス（CC0-1.0 または
  CC-BY-4.0）で記録を再配布する権利があると宣言したことになります。本
  プロジェクトのオープンソースライセンスは、ベンダーや取引所のデータに関する
  権利を与えるものではありません。宣言できない場合は共有をオフにしてください。
- 途中までの再生（`--limit-frames`）や途中で閉じたセッションは共有されません。

## その他のドキュメント（英語）

- [ARCHITECTURE.md](ARCHITECTURE.md): 仕組み、データ形式、アーカイブの改ざん対策と制限
- [PRIVACY.md](PRIVACY.md): プライバシーポリシー
- [CONTRIBUTING.md](CONTRIBUTING.md): 開発とテスト
- [tools/brisk_mock/README.md](tools/brisk_mock/README.md): Rust コレクター、フィールド定義、タイミングとレイテンシー
- [tools/brisk_mock/NAUTILUS_V2.md](tools/brisk_mock/NAUTILUS_V2.md): NautilusTrader v2 との連携
- [infra/README.md](infra/README.md): 独自アーカイブのデプロイ
- [THIRD_PARTY.md](THIRD_PARTY.md): デコーダーとデータの権利

ソフトウェアは MIT ライセンスです。
