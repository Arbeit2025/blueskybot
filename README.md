# blueskybot

Bluesky へ、決まった内容を毎日1回、自動で投稿するための Bot。
もともと X 向けに作ったスターターキット（ローカル `xbot`）を、
Bluesky（AT Protocol）向けに作り替えたもの。

## 大事な設計判断

- **費用はかからない。** Bluesky の API には有料プランや投稿ごとの
  課金がなく、アカウントとアプリパスワードがあれば投稿できる。
  制限は金額ではなくレート制限（ポイント制）で、毎日1投稿では
  事実上関係ない。
- **完全に同じ文面を毎日投稿しない。** 定型文の骨格は固定しつつ、
  テンプレートに `{date_ja}` と `{weekday_ja}` を含めて日付で
  差分を作る（`config/post.json`）。
- **dry-run が既定。** 実際に投稿するのは `--post` ＋ 環境変数
  `POST_ENABLED=true` ＋ 認証情報2点（ハンドル／アプリパスワード）
  が全部揃ったときだけ。
- **投稿専用。** いいね・フォロー・返信はしない。
- **常駐サーバーなし。** GitHub Actions の定期実行で回す。
- **リンクは facets で clickable にする。** API 経由の投稿では
  URL は自動でリンクにならないため、バイト位置の facets を付ける。

## 使い方（ローカル確認）

```bash
python -m unittest discover -s tests -v   # テスト
python -m bot.post_daily                  # dry-run：今日の投稿文を表示するだけ
```

## 設定

- 文面とタイムゾーン： `config/post.json` の `template`
  （使える置換： `{date}` / `{date_ja}` / `{weekday_ja}`、上限300字）
- 投稿時刻： `.github/workflows/daily-post.yml` の cron
  （既定は 09:00 JST = `0 0 * * *` UTC）
- 認証情報： `.env.example` を参照。実値は GitHub Secrets /
  ローカル `.env` のみに置き、リポジトリへ入れない。
  パスワードは本体パスワードではなく **アプリパスワード** を使う。

## 止め方（キルスイッチ）

GitHub の repository variable `POST_ENABLED` を `true` 以外にするか、
ワークフロー `daily-post` を Disable する。以後は dry-run のみになる。

詳しい導入手順は `docs/install.md`、設計は `docs/design.md`。
