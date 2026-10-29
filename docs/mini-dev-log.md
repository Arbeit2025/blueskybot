# ミニ開発記録（blueskybot）

このファイルは予測的不具合対策の実施記録。形式は「不具合予測 → 改善案 →
実装 → 複数回の動作確認 → 問題なければ記録」を1サイクルとする。

---

## 2026-10-29 Meta Muse 予測的不具合対策 5サイクル

- 担当AI: Meta Muse
- 対象リポジトリ/ブランチ: Arbeit2025/blueskybot / main（直接push方式は従来どおり）
- 開始時点の main: e2225b85（ChatGPT側 Cycle 5 の直後）
- 終了時点の main: a3f8cb3a（サイクルM5）。この記録自体のコミットが続く

### 事前調査（ChatGPT側の作業状況について）

ユーザーの認識は「ChatGPT側の work が途中で止まっている」だったが、実地確認
では ChatGPT 側は止まっておらず、2026-10-29 15:01〜16:04 JST にかけて
次が main へ直接コミットされていた。

- 2d024ca3: 外部 hourly fallback・独立監視・明示的な投稿結果
- 339942c6〜e2225b85: Cycle 1〜5（状態ファイル破損対策と原子的保存／
  全スロット検証とイベント検証／API応答の所有者・形式検証／安定レコード
  キーと並行・不確実投稿の照合／実行状態・フィード所有者・監視報告の検証）
- Muse が e2225b85 時点の全ファイルを読み戻し、単体テスト147件を
  3回実行して全て成功を確認した。ただしリポジトリ内にミニ開発記録
  ファイルは存在しなかった（本ファイルが初出）。ChatGPT側サイクルの
  記録は本ファイルには含めていない（担当AIが自分の作業だけを記録する）。
- 実投稿の現状（公開APIで確認）: 投稿は2件。09:08 JST の手動テスト
  「test」と、**16:06:54 JST の promo_afternoon（16:00宣伝枠）**
  （URI: at://did:plc:tgbrskgfguuhmg6jcl4wfzsk/app.bsky.feed.post/3myyoxafb22uu）。
  後者は定時／追いつき経路による初の通常枠の自動投稿で、ChatGPT側の
  スケジュール修正＋外部fallbackの効果とみられる。08:00〜12:00 の各枠は
  監視開始（14:49 JST）以前のため未投稿のまま（再送しない設計どおり）。

以下のサイクルM1〜M5は、上記 ChatGPT 側 Cycle 1〜5 と重ならない範囲から
選んだ。テスト総数は 147件 → 183件（新規36件）。

### サイクルM1: listRecords の取得順が「最古優先」になっていた（実証済み）

- 予測した不具合: 重複チェック用の `_list_recent_records` が
  `reverse=true` を送っており、「新しい順」と思い込んでいた。公開PDSで
  実地検証したところ reverse=true は **最古の投稿から** 返し、付けない
  場合が新しい順だった（2026-10-29 16:19 JST、実アカウントの2投稿で
  順序を確認）。このままだと投稿総数が100件（約12日分）を超えた時点で
  重複チェックが今日の投稿を永遠に見られなくなり、check_access の
  「最新投稿の直接照合」も最古の投稿を掴む。安定rkeyの getRecord が
  本命のガードのため即座に二重投稿にはならないが、予備ガードが
  静かに死ぬ構造だった。さらに cursor ページング未実装で、100件上限
  自体も頭打ちだった。
- 改善案: (1) reverse を送らない（API既定＝新しい順） (2) cursor を
  最大3ページ（300件）まで追う (3) cursor の形式検証 (4) 間違った
  前提を書いた既存テスト2件の主張を修正（test_client / test_http_integration）
- 実装: bot/bsky_client.py（_list_recent_records 再構成、
  _validate_record_page 分離）、tests/test_list_pagination.py 新規5件、
  既存テスト2件修正
- 動作確認: 全152件を3回成功。dry-run 正常終了。公開PDSでの順序実証済み
- コミット: ed327ac2

### サイクルM2: HTTP 429 の Retry-After を無視していた

- 予測した不具合: 一時障害リトライは固定の1秒・2秒バックオフのみで、
  サーバーが Retry-After で指定した待機時間を捨てていた。レート制限中
  （429）は指定より早く再試行して3回とも弾かれ、その枠は追いつき
  窓（2時間）頼みになる。
- 改善案: (1) HTTPError のヘッダから Retry-After（秒数形式）を取り出し
  BskyError に保持 (2) 待機時間は max(通常バックオフ, ヒント) とし、
  異常なヒントで実行が止まらないよう上限30秒 (3) _with_retry と
  create_post のリトライ待機の両方へ適用 (4) HTTP-date形式や不正値は
  無視して通常バックオフへ
- 実装: bot/bsky_client.py（_parse_retry_after / _retry_delay 追加）、
  tests/test_retry_after.py 新規7件
- 動作確認: 全159件を3回成功
- コミット: 38b796ba

### サイクルM3: URL末尾の句読点がリンクfacetに混入する

- 予測した不具合: URL抽出が貪欲な \S+ のため、文面が
  「…https://example.com/path。見てね」のように URL の直後に句読点や
  続きの文が来ると、句読点以降まで facet の URI に入り、リンク切れや
  PDS の facet 検証エラー（＝その枠の投稿失敗）になり得る。現在の
  本番文面は URL が行末で終わるため未発症で、文面変更時に踏む地雷。
- 改善案: (1) 末尾の英日句読点・閉じ括弧を除去（括弧は対応が取れて
  いる場合は残す） (2) 文中に現れる日本語の句読点・括弧の位置で
  マッチを切断（これらは正しいURIには現れない） (3) facet の
  バイト範囲は切り取った後のURLで再計算 (4) スキームだけになった
  マッチは facet にしない
- 実装: bot/template.py（clean_url 追加、build_link_facets 修正）、
  tests/test_facets_trim.py 新規9件
- 動作確認: 初回は全168件中2件失敗（空白なしで日本語文が続くケースを
  初版で処理できていなかった）。改善案(2)を追加して全168件を3回成功。
  本番文面の facet が従来と同一であることもテストで固定した
- コミット: 009e3027

### サイクルM4: 投稿経路でアカウント同一性と送信先を検証していなかった

- 予測した不具合: (1) 監視（health）は expected_did でフィードの
  所有者を検証していたが、投稿経路自体は未検証。Secrets に別アカウント
  のアプリパスワードが入ると、そのアカウントへ毎日投稿し続ける。
  (2) BLUESKY_PDS_URL を無検証で信用しており、誤設定や平文 http の
  ホストへアプリパスワードを送る可能性があった。
- 改善案: (1) create_post に expected_did 引数を追加し、セッションの
  DID が config/operations.json の expected_did と違う場合は投稿前に
  拒否（IdentityMismatch、結果は authentication_error として報告）
  (2) operations.json が壊れている場合は guard を黙って無効化せず
  configuration_error で停止 (3) PDS URL は https 必須（http は
  ループバックのテストサーバーだけ許可）、認証情報埋め込み・
  クエリ・断片は拒否 (4) check_access 診断でも同一性を確認。
  既存テスト3件には、偽アカウントを使う前提に合わせて
  load_expected_did の差し替えを追加した
- 実装: bot/bsky_client.py（validate_pds_url、create_post の照合）、
  bot/post_daily.py（load_expected_did と配線）、bot/check_access.py、
  tests/test_identity_guard.py 新規12件、既存テスト3件調整
- 動作確認: 全180件を3回成功。dry-run 正常終了。実アカウントの DID が
  operations.json の expected_did と一致することは公開プロフィールで
  確認済み（did:plc:tgbrskgfguuhmg6jcl4wfzsk）のため、19:00枠以降も
  この guard で止まらない見込み
- コミット: ce4582f8

### サイクルM5: リトライ時に createdAt が古いまま／状態ファイルの無限肥大

- 予測した不具合: (1) 投稿レコードをリトライの前に1回だけ作っていた
  ため、createdAt が初回試行の時刻で固定される。M2 で Retry-After を
  尊重するようになった分だけ待機が長くなり得て、古い createdAt が
  重複チェックや監視の時間窓から外れると「未投稿」誤判定の原因になる。
  (2) 状態ファイルの posted 記録は日々増えるだけで、ローカル常駐運用
  では無限に肥大する（読むのは今日のキーだけ）。
- 改善案: (1) レコードとペイロードを試行ごとに再生成（安定rkeyが
  あるため安全） (2) 状態保存時に記録日より7日より前のエントリを
  整理。日付として解釈できないキーは消さずに残す（未知データの
  盲目削除をしない）
- 実装: bot/bsky_client.py（create_post のループ内生成）、
  bot/state.py（_prune_posted、STATE_RETENTION_DAYS=7）、
  tests/test_freshness.py 新規3件
- 動作確認: 全183件を3回成功。dry-run 正常終了
- コミット: a3f8cb3a

### 全体確認と残課題

- 最終確認: 変更した10ファイルを GitHub main から読み戻し、ローカルと
  全て一致（blob 一致）。全183件テストを3回成功
- 本番反映状況: M1〜M5 はいずれも main へ直接 push 済みのため、
  次の自動実行（本日19:00 JST の greeting_evening 以降）から新しい
  コードで動く。**19:00枠の実行結果はこの記録の時点では未確認**
  （投稿の成否は公開フィード／Actions で別途確認すること）
- 未確認: GitHub Actions 上での CI 実行結果（接続ツールの範囲では
  未取得）。実投稿を伴う検証は行っていない（dry-run とスタブ、
  ローカルHTTP統合テストまで）
- 残課題: (1) ChatGPT側 Cycle 1〜5 と 2d024ca3 のミニ開発記録が未作成
  (2) 文面に空白なしで日本語が続くURLは句読点位置でのみ切断でき、
  句読点なしで地の文が続く場合は判別できない（文面作成時の注意として
  残る） (3) 19:00以降の各枠が新コードで正常投稿されるかの継続確認
