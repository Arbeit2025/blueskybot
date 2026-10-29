# 導入手順

## 1. Bluesky でアプリパスワードを作る

1. Bluesky にログイン（bot を動かすアカウント）
2. 設定 → プライバシーとセキュリティ → アプリパスワード
（英語 UI では Settings → Privacy and Security → App Passwords）
3. 「アプリパスワードを追加」で名前（例：`blueskybot`）を付けて発行
4. 表示される `xxxx-xxxx-xxxx-xxxx` 形式のパスワードを控える
（この画面でしか表示されない。本体パスワードとは別物で、
このパスワードだけを単独で失効できる）

認証情報はチャットや公開リポジトリに貼らないこと。

## 2. GitHub リポジトリへ置く

1. このフォルダの中身をリポジトリ `blueskybot` へ push
（既存の githubbot 標準セット `.github/labeler.yml` などは消さない）
2. Settings → Secrets and variables → Actions：
- Secrets: `BLUESKY_HANDLE`（例：`your-handle.bsky.social`）/
`BLUESKY_APP_PASSWORD`（手順1のアプリパスワード）
- Variables: `POST_ENABLED=false` を明示して意図的な停止状態で始める。
  未設定は設定エラーになる。手動の既定プレビューは引き続き投稿しない。
3. Actions タブで `daily-post` を手動実行（workflow_dispatch）し、
slot=all の dry-run で全9スロットの投稿文が表示されることを確認

## 3. 本番化

1. `config/post.json` のスロット文面を本決まりの文面へ書き換える
2. Variables に `POST_ENABLED=true` を追加
3. `daily-post` を手動実行し、slot を1つ選んで dry_run=false で
1件だけ実投稿し、Bluesky 側で表示とリンクを確認
4. 以後は毎時03・13・23・33・43・53分に起動し、`config/post.json`
の予定時刻（JST 01:00／08:00／09:00／10:00／12:00／16:00／19:00／
20:00／22:00）を過ぎた未投稿枠を確認する。通常は予定の約3分後。
起動に遅れや取りこぼしがあっても2時間以内なら次の起動で回収する。
2時間を超えた枠はまとめて投稿しない。GitHub 側が起動し続けない
場合の復旧や、時刻ぴったりの投稿を保証する仕組みではない。
5. 手動で回収を確認するときは slot=due、dry_run=true でプレビュー
できる。dry_run=false では未投稿枠を実際に投稿する。

## 4. keepalive の確認

公開リポジトリでは、60日間リポジトリ活動がないと GitHub が
スケジュール実行を自動無効化する。`.github/workflows/keepalive.yml`
が月1回空コミットを push して活動を維持するので、Actions タブで
`keepalive` を手動実行し、成功すること（空コミットの push が
権限で拒否されないこと）を1回確認しておく。失敗する場合は
Settings → Actions → General → Workflow permissions が
「Read and write permissions」になっているか確認する。

## 5. プロフィールの明示（推奨）

Bot 投稿には文頭の表記で手動投稿と区別している。
プロフィールにも「一部自動投稿あり」と書いておくと分かりやすい。
