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
- Variables: `POST_ENABLED` は最初は作らない（＝dry-run のまま）
3. Actions タブで `daily-post` を手動実行（workflow_dispatch）し、
dry-run で投稿文が表示されることを確認

## 3. 本番化

1. `config/post.json` の定型文を本決まりの文面へ書き換える
2. Variables に `POST_ENABLED=true` を追加
3. `daily-post` を手動実行（dry_run=false）して1件だけ実投稿し、
Bluesky 側で表示とリンクを確認
4. 以後はスケジュール（既定 09:00 JST）で毎日自動投稿される

## 4. プロフィールの明示（推奨）

Bot 投稿には文頭の表記で手動投稿と区別している。
プロフィールにも「一部自動投稿あり」と書いておくと分かりやすい。
