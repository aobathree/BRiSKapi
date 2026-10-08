# DONE（フォーク `aobathree/BRiSKapi` の `e-shiten` ブランチ。新しいものが上）

このファイルはフォーク固有の作業記録で、上流 honvl/BRiSKapi には送らない。
このブランチは上流追随と、将来 PR を出す場合の土台。自分用ツールは
`../aobathree-brisk`（`bakyo`）にあり、このリポジトリに依存しない。

## 2026-10-08: 立花証券e支店の BRiSK Next を第 2 のサイトとして追加

### コミット

| コミット | 内容 |
| --- | --- |
| 9907fba | `feat: Tachibana e-shiten BRiSK Next as a second broker site` |
| 4ba897f | `fix: find the decoder through relative script paths and templated asset paths` |

### 変更の要点

- **サイト表** `sbi` / `e-shiten` を `briskapi/sbi.py` と `briskapi/decoder/sbi.cjs` に追加。
  `sbi.login(site="e-shiten")`、`brisk live --e-shiten`、Node ホストへは `BRISK_SBI_SITE`。
  保存 Cookie はサイト別ファイル（`~/.config/brisk/e-shiten-cookies.json`）。
- **WebSocket の解決**: `/api/app/boot` の `ws_url` は `/` 始まりの相対パスで、ブラウザは
  `/api/frontend/boot` の `api_endpoint`（`https://api.brisk.jp`）を基準に解決している。
  ホストも同じ解決にし、Cookie はログイン元と同じホストのときだけ付け、`Origin` ヘッダーを
  サイトにし、`brisk.jp` 以外のホストは拒否する。pybrisk のサンプルでも SBI の
  `api_endpoint` は同じ `api.brisk.jp` なので、SBI 版にも効く修正。
- **デコーダー探索**: e支店のページはスクリプトを相対パス（`main-XXXX.js`）で読み、
  バンドル内のデコーダーのパスはテンプレート文字列 `` `./assets/wasm${r}/fita.<hash>.js` ``
  （`r` は福岡取引所向けの `-fukuoka` 切り替え）。両方に対応し、`.wasm` 名は
  Emscripten の慣例どおり `.js` から導く。
- **プロトコル版の上書き**: `sbi.connect(protocol_version=...)` / `BRISK_SBI_PROTOCOL_VERSION`。
  後に `/api/app/boot` の `flex_version` が 18000 と分かり、e支店ビルドも SBI と同じ版。
- タイミング報告の `source` に `eshiten_live` を追加（`schema.TIMING_SOURCES`）。
  `POLICY_VERSION` は上げていない（収集項目は変わらず、値が 1 つ増えただけ）。
  上流のアーカイブサービスが `eshiten_live` を受け付けるのは上流に取り込まれてから。
- README 日英、ARCHITECTURE、PRIVACY、CONTRIBUTING を同期。

### 実機で確認したこと（e支店口座、2026-10-08）

| 段階 | 結果 |
| --- | --- |
| Cookie 認証、`/api/frontend/boot`、`/api/app/boot` | 通る |
| REST（schedule、5 分足 132 本、日足 119 本、信用残 243 日、売買代金 4,557 銘柄、銘柄リスト、市場イベント 473〜641 件、ウォッチリスト） | **全て動作** |
| デコーダー探索・取得（`fita.35e38ad5….js/.wasm`）、ABI 検査（必要関数は全て存在。`_ohlcLength`、`_getPortfolios`、`_getQR` など追加あり） | 通る |
| マスター（4,443 銘柄、800 KB）、スナップショット（42 MB）の取得 | 通る |
| `Decoder` 生成時のマスター構造体の解析（`decoder.cjs` の 144 バイト前提） | **失敗**（`Unsafe 64-bit quantity/timestamp`、`lot_size` の読み取り）。e支店ビルドの構造体はデモ版と一致しない |
| WebSocket 流路 | 未到達。ブラウザの観察では受信方向のバイナリ 32〜616 B、約 5 ms 間隔 |

### 止めた作業と理由

マスター構造体の内部レイアウトの解析は、BRiSK 社の独自 WASM のメモリ内構造の
リバースエンジニアリングにあたり、Claude 側の安全分類器で停止した。**再開しない。**
ライブ接続はこの地点で未達。上流の作者（元々立花証券向けを想定）が対応すれば再評価する。

### テスト

- Python: 162 passed（カバレッジ 98.8%）。Node: 18 passed（デモ資産あり）。fixture reference: 2 passed。
- Rust は変更していないため未実行。

### 判断

- PR・Issue は現時点で出さない（ユーザー判断、2026-10-08）。
- 「BRiSK Next for 立花証券」利用規定 4.(3) は蓄積・編集・加工・二次利用を禁じる。
  ユーザーは原文精読のうえ、自分専用・再配信なし・高負荷をかけない前提でリスク受容
  （記録は `../aobathree-brisk/docs/06-estn-and-brisk.md` §4a）。
