# blueskybot

Bluesky へ、決まった内容を1日9回、自動で投稿するための Bot。
もともと X 向けに作ったスターターキット（ローカル `xbot`）を、
Bluesky（AT Protocol）向けに作り替えたもの。

## 1日の投稿（全て JST）

| 時刻 | 種別 | 内容 |
|---|---|---|
| 01:00 | 募集 | こんばんは＋ゲーム案募集＋ミニゲーム集URL |
| 08:00 | 挨拶 | おはよう |
| 09:00 | 募集 | おはよう＋ゲーム案募集＋ミニゲーム集URL |
| 10:00 | 宣伝 | こんにちは＋ミニゲーム開発中＋URL |
| 12:00 | 挨拶 | こんにちは |
| 16:00 | 宣伝 | こんにちは＋ミニゲーム開発中＋URL |
| 19:00 | 挨拶 | こんばんは |
| 20:00 | 募集 | こんばんは＋ゲーム案募集＋ミニゲーム集URL |
| 22:00 | 挨拶 | おやすみ |

スロットの定義と文面は `config/post.json` の `slots` に集約してある。
文面は 2026-10-29 にユーザーが指定した固定文（プレースホルダなし）。
そのため同じ文面が毎日そのまま投稿される（Bluesky は重複投稿を
拒否しない）。

## 大事な設計判断

- **費用はかからない。** Bluesky の API には有料プランや投稿ごとの
  課金がなく、アカウントとアプリパスワードがあれば投稿できる。
  制限は金額ではなくレート制限（ポイント制）で、1日9投稿でも
  事実上関係ない。
- **文面はユーザー指定の固定文。** プレースホルダ（`{date_ja}` 等）
  の仕組みは残してあるが、現在の文面では使っていない。
- **dry-run が既定。** 実際に投稿するのは `--post` ＋ `--slot` ＋
  環境変数 `POST_ENABLED=true` ＋ 認証情報2点（ハンドル／
  アプリパスワード）が全部揃ったときだけ。
- **投稿専用。** いいね・フォロー・返信はしない。
- **常駐サーバーなし。** GitHub Actions の定期実行で回す。
- **リンクは facets で clickable にする。** API 経由の投稿では
  URL は自動でリンクにならないため、バイト位置の facets を付ける。

## 使い方（ローカル確認）

```bash
python -m unittest discover -s tests -v   # テスト
python -m bot.post_daily                  # dry-run：全9スロットの文面を表示
python -m bot.post_daily --slot promo_morning   # dry-run：1スロットだけ
```

## 設定

- 文面と時刻： `config/post.json` の `slots`（各スロットに
  `id` / `time_jst` / `template`。使える置換： `{date}` /
  `{date_ja}` / `{weekday_ja}` / `{slot_time}`、上限300字）
- 投稿時刻： `.github/workflows/daily-post.yml` の cron 9本
  （UTC表記。cron文字列とスロットの対応は同ファイル内に記載）
- 認証情報： `.env.example` を参照。実値は GitHub Secrets /
  ローカル `.env` のみに置き、リポジトリへ入れない。
  パスワードは本体パスワードではなく **アプリパスワード** を使う。

## 止め方（キルスイッチ）

GitHub の repository variable `POST_ENABLED` を `true` 以外にするか、
ワークフロー `daily-post` を Disable する。以後は dry-run のみになる。

詳しい導入手順は `docs/install.md`、設計は `docs/design.md`。
