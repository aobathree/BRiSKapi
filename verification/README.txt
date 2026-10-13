briskapi 0.7.0 ライブ接続の切り分け（aobathree、2026-10-12〜13）

スクリプト（briskapi 0.7.0 の Decoder / sbi.cjs をそのまま使う黒箱テスト。データは含みません）
  t9_live_site_070.py          sbi.connect() を 60 秒試す（共有・録画なし）
  t10_boot_assets_070.py       boot の内容（トークン・ws_url は出さない）とマスター/スナップショットのサイズ
  t11_fetch_and_bisect_070.cjs 当日のデコーダー・マスター・スナップショットを保存し、スナップショット有無で Decoder.create を切り分け
  t12_snapshot_bisect_070.cjs  スナップショットのレコード部分集合と、pybrisk の 2026年3月デコーダーでの切り分け

ログ
  *_070.log   10月12日（祝日。boot に ws_url なし、10月9日の終了済みスナップショット 48 MB）→ deque
  *_1013.log  10月13日 08:44（寄り前。ws_url あり、スナップショット 13.9 MB）→ Invalid unserialize
  t11_sha256_*.log  デコーダー・マスター・スナップショットの SHA256（e支店と SBI は同一ビルド）

実行例
  node t11_fetch_and_bisect_070.cjs <site-packages/briskapi> <保存先> e-shiten
  node t12_snapshot_bisect_070.cjs  <site-packages/briskapi> <保存先> <pybrisk 3月版の cache>

0.7.1 での WebSocket 調査（2026-10-13 後場）
  t13_ws_handshake_071.cjs      ホストと同じ URL へ Upgrade 要求（ヘッダー変種 A〜F）、ステータスのみ記録
  t15_ws_handshake_ua_071.cjs   Chrome 風ヘッダー等の変種 G〜J
  t14_ws_session_identity_071.cjs  ws_url の session / api_token が boot ごとに変わるか（ハッシュ比較）
  t9_live_*_071.log             sbi.connect() の結果：schedule applied (66 price bars) → WebSocket 502
