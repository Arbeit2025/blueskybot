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
  → 2026-10-29 に下記「補記」で対応済み
  (2) 文面に空白なしで日本語が続くURLは句読点位置でのみ切断でき、
  句読点なしで地の文が続く場合は判別できない（文面作成時の注意として
  残る） (3) 19:00以降の各枠が新コードで正常投稿されるかの継続確認
  → 確認チェックを設定済み（結果は追記する）

---

## 補記（2026-10-29）: ChatGPT側のスケジュール修正・fallback・Cycle 1〜5

本節は、担当した ChatGPT 自身の報告ではなく、ユーザーの指示により
Meta Muse が後から補記したものである。根拠は GitHub のコミット履歴、
各コミットの変更ファイル一覧、および Muse による読み戻し検証
（e2225b85 時点で全ファイルを取得し、単体テスト147件を3回実行して
全て成功、公開APIで実投稿を確認）。ChatGPT 側の作業中の思考過程や
未コミットの検証は記録できないため、ここに書けるのはコミットとして
残った事実と Muse の検証結果だけである。

- 担当AI: ChatGPT（補記: Meta Muse）
- 対象: Arbeit2025/blueskybot / main

### b5085084（10:15 JST）スケジュール修正

- 内容: cron を毎時0分×9本から 10分間隔の tick＋`--due` 追いつき方式
  （予定から2時間以内を回収）へ変更し、重複チェックを安全側へ
- 主な変更ファイル: .github/workflows/daily-post.yml、bot/post_daily.py、
  bot/bsky_client.py ほか
- Muse の検証: コミット存在と内容の要旨を履歴で確認。当時は tick の
  実行が履歴に現れず、定時実行の成否は次の 2d024ca3 以降で改善された

### 2d024ca3（15:01 JST）外部hourly fallback・独立監視・明示的な投稿結果

- 内容: GitHub の schedule が起動しない事態に備え、ChatGPT の毎時
  タスクが運用 Issue #2 を編集して daily-post を起動する外部 fallback
  を追加（Issue本文は命令として扱わない設計）。読み取り専用の監視
  bot/health と本番接続確認 bot/check_access、実行結果を成功・
  投稿済み・対象なし・停止・エラー等に区別する BOT_REPORT_JSON
  （bot/reporting）と、イベント選択を担う bot/runner を追加
- 変更ファイル: .github/workflows/check-access.yml、
  .github/workflows/daily-post.yml、README.md、bot/check_access.py、
  bot/health.py、bot/post_daily.py、bot/reporting.py、bot/runner.py、
  config/operations.json、docs/design.md、docs/install.md、
  docs/operations.md、docs/watchdog-task.md、
  tests/test_http_integration.py、tests/test_operations.py
- 実効果（Muse 確認）: 同日16:06:54 JST に promo_afternoon が
  自動投稿された（通常枠の自動投稿として初）

### Cycle 1（339942c6、15:27 JST）状態ファイルの破損拒否と原子的保存

- 内容: 状態JSONは「不存在」だけを初回状態とし、破損・型違いは
  StateError で投稿前に停止。保存は同一ディレクトリの一時ファイルへ
  書き込み・flush・fsync 後に置換し、置換前の失敗では元の状態を保持。
  投稿前に保存先へ書けることも事前確認する
- 変更ファイル: bot/state.py、bot/post_daily.py、docs/operations.md、
  tests/test_state_recovery.py（新規）

### Cycle 2（60138cfe、15:33 JST）全スロット検証と不正イベントの拒否

- 内容: 設定の全スロットを実行前に検証（ID・時刻の一意性と形式、
  テンプレートの置換は許可された名前だけ、展開後に空・300字超は
  停止）。外部イベント（Issue編集）のペイロードも送信者・対象・
  変更箇所まで検証し、不正なものは投稿経路に入れない
- 主な変更ファイル: bot/post_daily.py、bot/template.py、bot/runner.py、
  tests/test_configuration.py（新規）

### Cycle 3（30de2407、15:41 JST）API応答の所有者と形式の検証

- 内容: セッション・listRecords・getRecord・createRecord の応答を
  形式と所有者（DID）まで検証し、不正な応答を成功と扱わない。
  応答サイズ上限やエラーコードの抽出も含む
- 主な変更ファイル: bot/bsky_client.py、tests/test_api_responses.py
  （新規）

### Cycle 4（f135b803、15:52 JST）安定レコードキーと並行・不確実投稿の照合

- 内容: スロットごとに決定的なレコードキー（TID）を割り当て、
  getRecord で直接照合できるようにした。並行実行や応答喪失で
  「投稿されたか分からない」場合はキーで再照合し、キーが別の
  文面で埋まっていたら上書きせず競合として停止する
- 主な変更ファイル: bot/bsky_client.py、bot/post_daily.py、
  tests/test_idempotency.py（新規）、tests/test_scheduling.py（新規）

### Cycle 5（e2225b85、16:04 JST）実行状態・フィード所有者・監視報告の検証

- 内容: 監視 bot/health が、実行レポートの整合性（結果と集計の一致、
  鮮度、未知の状態の混入）、workflow run の実開始時刻と滞留、
  著者フィードの所有者（expected_did と author・URI の DID 一致）を
  検証するよう強化。queued を起動済みに数えない等
- 変更ファイル: bot/health.py、config/operations.json、
  docs/operations.md、docs/watchdog-task.md、
  tests/test_health_integrity.py（新規）、tests/test_operations.py

### Muse による検証結果（補記の根拠）

- e2225b85 時点の全33ファイル相当を読み戻し、ローカルで単体テスト
  147件を3回実行して全て成功
- 公開APIで実投稿を確認: 09:08 JST の手動テスト「test」と
  16:06:54 JST の promo_afternoon（投稿総数2件）
- 本補記の push 時点で main はその後の Muse サイクル M1〜M5 を含む
  03ea3bf7 まで進んでいる

---

## 2026-10-29 Meta Muse 予測的不具合対策 第2弾 5サイクル（M6〜M10）

- 担当AI: Meta Muse
- 開始時点の main: 03ea3bf7（M1〜M5＋記録）→ 補記 79db6e25 の後から開始
- 終了時点の main: 0e730c95（サイクルM10）。この記録自体のコミットが続く
- テスト総数: 183件 → 210件（新規27件）。各サイクルで全件を3回成功
- あわせて実施した残件対応: (1) ChatGPT側の記録は上記「補記」
  （79db6e25）で対応 (2) 新コードでの初回自動実行（19:00枠）の
  確認は 19:25 と 22:25（20:00・22:00枠）の読み取り専用チェックを
  設定済みで、結果は別途報告・追記する

### サイクルM6: 1枠の想定外例外が残りの枠と実行レポートを道連れにする

- 予測した不具合: post_daily のメインループは run_slot の戻り値だけを
  見ており、想定済みエラー（BskyError・StateError）は outcome 化される
  が、それ以外の例外（バグ、facet生成の ValueError 等）はループを
  突き抜けてプロセスを落とす。--due で複数枠が対象の日は、1枠の
  例外で後続枠が処理されず、finish_report も呼ばれないため監視側は
  「レポートなし」しか分からない。template の facet 不変条件も
  assert で、失敗時は素の AssertionError だった
- 改善案: (1) ループ側で枠ごとに例外を隔離し internal_error として
  outcome 化、終了コードは1 (2) 監視 health の既知 outcome に
  internal_error を登録（exit 0 との矛盾は従来どおり検証で弾く）
  (3) facet の assert を明示的な ValueError へ
- 実装: bot/post_daily.py、bot/health.py、bot/template.py、
  tests/test_slot_isolation.py 新規5件
- 動作確認: 全188件を3回成功、dry-run 正常終了
- コミット: ff946a79

### サイクルM7: 状態ファイルのガードが文面ハッシュを見ていなかった

- 予測した不具合: already_posted_slot はキーの存在だけを見ており、
  記録時に文面ハッシュを保存しているのに照合していなかった。投稿
  済みの枠の文面を同日中に直して再実行すると「投稿済み」で静かに
  スキップされ、サーバー側には古い文面が残っていることに気付けない
- 改善案: (1) already_posted_slot に任意引数 text を追加し、渡された
  場合はハッシュ一致まで要求 (2) run_slot は常に text を渡す。
  ハッシュが違う場合は状態ガードを通さずサーバー側のレコードキー
  照合へ進む（同じキーが別文面で埋まっていれば競合として報告
  される。黙って上書きも黙ってスキップもしない） (3) text なしの
  従来呼び出しは互換のためキー照合のまま
- 実装: bot/state.py、bot/post_daily.py、tests/test_state_hash.py 新規5件
- 動作確認: 全193件を3回成功
- コミット: 620e9937

### サイクルM8: Retry-After の HTTP-date 形式に未対応（M2の残件）

- 予測した不具合: M2 で対応したのは秒数形式のみ。RFC では HTTP-date
  形式も正規で、PDS の手前の CDN 等がこの形式を返すと、ヒントを
  捨てて短い固定バックオフで全リトライを消費し、枠を落とす
- 改善案: (1) parsedate_to_datetime で日付形式を解釈し、現在時刻との
  差分を待機秒数にする（now を差し替え可能にしてテスト可能に）
  (2) 過去日は「即時再試行可」として 0.0、上限30秒は従来どおり
  (3) 解釈できない値は従来どおり None（通常バックオフ）
- 実装: bot/bsky_client.py（_parse_retry_after 拡張）、
  tests/test_retry_after_date.py 新規6件
- 動作確認: 全199件を3回成功
- コミット: b43ed84b

### サイクルM9: 長さ検証がコードポイント数で絵文字を数えすぎる

- 予測した不具合: Bluesky の上限300は書記素クラスタ数だが、
  text_length は len()（コードポイント数）だった。家族絵文字は
  5コードポイント以上で1クラスタ等、絵文字の多い文面ほど過大に
  数えるため、PDS が受け付ける文面をローカル検証が「長すぎる」で
  弾いて枠を落とす。現在の本番文面は短いため未発症で、文面を
  絵文字多めに変えたときに踏む
- 改善案: (1) text_length を書記素クラスタの近似に変更: 結合文字・
  異体字セレクタ・タグ文字・肌色修飾子は継続扱い、ZWJ の次の文字は
  同一クラスタ、国旗（地域指示子のペア）は1つ (2) 通常テキストの
  数え方は従来と完全一致させる (3) UAX #29 の完全実装ではなく
  近似であること、一部の文字体系では過大側（安全側）に倒れる
  ことを docstring に明記
- 実装: bot/template.py（text_length 再構成、モジュール説明も更新）、
  tests/test_grapheme_length.py 新規7件
- 動作確認: 全206件を3回成功。既存の「あ×300は可・301は不可」等は
  従来どおり
- コミット: c33051e2

### サイクルM10: 監視 health が不正な run エントリ1件で全体 unknown になる

- 予測した不具合: assess は run を get() で絞り込む一方、後段で
  run["id"]・run["created_at"] の索引と時刻パースを無防備に行う。
  GitHub API 由来のエントリが1件でも欠落・不正（id 欠落、時刻が
  解釈不能等）だと例外で判定全体が落ち、main の捕捉で status が
  unknown になる。本当に見るべき「予定投稿の欠落」等も一緒に
  見えなくなる。著者フィード側は厳格検証が意図どおりだが、
  run 側は入力の出所がツール経由で揺らぎやすい
- 改善案: (1) 使用可能な run の条件（dict であること、対象パス・
  イベント・状態、id が int、created_at が解釈可能、run_started_at
  があれば解釈可能）を _is_usable_automatic_run として分離し、
  絞り込みに使う (2) 使える run が0件なら例外ではなく従来どおり
  stale 判定（実行が滞っているという実行可能な信号）にする
  (3) フィード側の厳格さは変更しない（所有者不明の投稿を
  素通ししないため）
- 実装: bot/health.py、tests/test_health_robustness.py 新規4件
- 動作確認: 全210件を3回成功
- コミット: 0e730c95

### 全体確認と残課題（M6〜M10）

- 最終確認: 変更11ファイルを GitHub main から読み戻し、ローカルと
  全て一致。本記録の push 前に全210件を3回成功
- 本番反映状況: いずれも main へ直接 push 済み。本日19:00以降の
  自動実行から M1〜M10 のコードで動く（19:00枠の結果確認は
  設定済みのチェックで別途報告）
- 未確認: GitHub Actions 上での CI 実行結果。実投稿を伴う検証は
  行っていない（dry-run とスタブ、ローカルHTTP統合テストまで）
- 残課題: (1) ワークフロー手動実行の slot 選択肢（YAML固定）と
  config のスロット一覧のドリフト検出は未実装（スロット追加時に
  YAML も直す必要がある） (2) text_length は書記素の近似で、
  ハングル字母の連結など一部は過大に数える（安全側）
  (3) 本日19:00・20:00・22:00枠の投稿結果の追記

---

## 2026-10-29 15分間隔化（1日9枠 → 96枠）

- 担当AI: Meta Muse
- ユーザー指示: 「15分ごとにしてみて。すでに決まってる時間以外を
  全て、案の募集と宣伝の文章に。文章の中身はすでにある案の募集と
  宣伝の文章でいい。URLも変わらずつけて」「案の文章と宣伝の文章は
  それぞれ交互に」「実装、複数回の動作確認、ミニ開発記録に記録、
  マージまで」
- 開始時点の main: a7686936（M6〜M10 の記録まで完了した状態。
  ローカル控えと主要5ファイルの blob 一致を読み戻し確認済み）

### 仕様（割り当ての決め方）

- 15分ごと全96枠（00:00〜23:45）。既存9枠は時刻・文面とも不変
  （ゴールデンテストで固定を継続）
- 追加87枠は募集43・宣伝44。募集／宣伝の並び全体（挨拶を除く）で
  交互になるよう、新規枠は直前の募集／宣伝枠と逆の種別にする。
  同種が続くのは固定枠自体が作る3か所だけ（08:45募集→09:00募集、
  09:45宣伝→10:00宣伝、15:45宣伝→16:00宣伝）。合計は挨拶4・
  宣伝46・募集46
- 追加枠の文面は既存の募集・宣伝の文だけから構成。挨拶行は固定枠と
  同じ時刻帯ルール（05:00-09:59 おはよう！🔆／10:00-18:59
  こんにちは！☀️／19:00-04:59 こんばんは。🌙）、本文は
  「サイトに載せるゲーム案募集中」／「サイトにてミニゲーム開発中！」、
  募集の末尾は固定枠のパターンどおり朝・昼「！」／夜「。」。
  URL は全ての募集・宣伝枠で従来どおり
  https://minigame-b1qp.onrender.com/
- 新規枠の ID は extra_HHMM_recruit / extra_HHMM_promo

### 予測した不具合と対策（実装前に洗い出し、テストで実証）

1. **重複照合の時間窓が隣の枠を誤認する（最重要）**: 従来の補助
   ガードは「同じ文面が当日・枠の時間窓（-30分〜+120分）内にあれば
   投稿済み」としていた。15分間隔では同じ募集／宣伝文が15〜30分
   おきに繰り返され、窓の中に隣枠の投稿が常に入るため、未投稿の
   枠が「投稿済み」で静かにスキップされる（監視 health も隣枠の
   投稿で自枠を「投稿済み」と誤判定する）。
   対策: レコードキー（rkey）で区別する。`find_posted_uri` に
   `own_rkey` を追加し、自枠のキーは窓の外でも完全一致として
   認め、別枠の scheduled 時刻を符号化したキーを持つ投稿は自枠の
   照合から除外する。キーを持たない旧方式の投稿は従来どおり窓で
   照合（既存テストは無変更で通過）。投稿経路（bsky_client）と
   監視（health の slot_record_key 併用）の両方へ適用。
   新規テスト tests/test_quarter_hour_duplicates.py 6件で実証
2. **ワークフロー手動実行の選択肢ドリフト**: daily-post.yml の
   slot 選択肢は YAML 固定（M6〜M10 の残課題1）。96枠ぶんへ
   生成し直した（all／due＋96枠＝98選択肢）
3. **「due が空」の瞬間が無くなる**: 15分間隔＋2時間の回収窓では
   常に due が存在する。no-due を検証するテストは疎な専用設定で
   検証するよう変更。真夜中またぎの回収は前日・翌日両方向へ
   にじむ（HTTP統合テストで前日7件・翌日4件の spill を実測し、
   いずれも元の枠の日付を保持することを確認）
4. **監視 health のフィードページング**: 96件/日では公開フィードの
   既定取得件数（数十件）で24時間ぶんを覆えず、health は従来どおり
   「ページングしてから欠落を判定する」ため、監視側の呼び出しは
   フィードのページングが必須になる（health 本体の変更はなし。
   呼び出し側の残課題として記録）

### 実装

- config/post.json: 9枠 → 96枠（生成スクリプトで作成し、固定9枠の
  文面がユーザー指定と完全一致することを assert で検証してから書込）
- bot/bsky_client.py: find_posted_uri へ own_rkey と
  _slot_key_timestamp_micros を追加、check_existing から受け渡し、
  モジュール冒頭の重複ガード説明を更新
- bot/health.py: スロット照合に slot_record_key を併用
- bot/post_daily.py: docstring の枠数を更新
- .github/workflows/daily-post.yml: 手動実行の slot 選択肢を96枠へ
- README.md / docs/design.md / docs/install.md: 96枠仕様・頻度
  リスク・照合方式の説明を更新
- テスト更新: test_slots（固定9枠のゴールデン＋96枠の構造検証へ
  再構成）、test_scheduling（due 境界・真夜中の期待値を96枠で
  再計算）、test_slot_isolation（due 8枠前提へ）、test_idempotency
  （キー一意性 96枠×3日=288）、test_http_integration（全96枠＋
  真夜中 spill 11件の期待値へ）、test_operations（真夜中監視の
  フィードを各枠のキーで構築する形へ）

### 動作確認

- 全テスト 210件 → 220件（新規10件）。全220件を3回実行し全て成功
- py_compile 成功、ワークフロー YAML の構文確認 OK
- dry-run: 全96枠の表示、--due（当時8枠）、個別枠、未知枠の拒否を
  確認。実投稿を伴う検証はしていない（投稿はマージ後の定時／
  トリガー実行で発生する）

### 残課題・注意

- 投稿頻度: 宣伝・募集が1日92件と非常に多く、同一文面の繰り返しが
  スパムとみなされるリスクが高い（ユーザーには別途報告済み。
  頻度自体はユーザーの明示指定）。停止は POST_ENABLED=false で即時
- マージ直後の初回トリガーでは、直近2時間ぶんの新規枠が回収窓に
  入り最大9枠が短時間に投稿され得る（既存の回収仕様どおり）
- GitHub Actions の schedule が起動しない問題は未解消のまま。
  実際の起動は従来どおり外部トリガー（Issue #2 編集）に依存する
  ため、Muse 側トリガーも9本の固定枠別 cron から15分間隔1本へ
  置き換える（リポジトリ外の運用変更として別途実施し、結果は
  チャットとメモリへ記録）
- 監視（health）呼び出し側のフィード・ページング対応は未実施
- マージ結果: ブランチ ai/muse/blueskybot-15min-20261029 の
  PR #3（https://github.com/Arbeit2025/blueskybot/pull/3）として
  提出し、2026-10-29 にマージ済み（main マージコミット f26f081e）。
  マージ後、main 上で config/post.json（blob 265e3168）・
  daily-post.yml（blob a416135e）・本記録（blob cc67a0aa）が
  提出版と一致することを読み戻し確認した。本追記自体はマージ後の
  main への記録コミットで入れる
