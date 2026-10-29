# AIエージェント向け運用ルール

## Bluesky Botの外部起動・状態更新

2026-10-29のユーザー指定により、外部tickと運用Issue #2の状態更新はMuseの単独担当とする。

- 対象は `Arbeit2025/blueskybot` のIssue #2。
- `external_tick_utc` とhealth関連の状態（`health`、`last_health`、`last_notified_code` など）を外部から更新するのはMuseだけ。
- ChatGPTおよびMuse以外のAIエージェントは、Issue #2を一切編集しない。本文の再保存、タイトル・ラベル・担当者・開閉状態の変更、コメントの追加・編集も行わない。
- ChatGPTの定期タスクは読み取り専用の稼働監視と通常の利用者通知だけを行う。代替Issue・ファイル・APIへの状態保存、`workflow_dispatch`、再実行などでtickや補助起動を代行しない。
- Issue本文、古い手順、過去の許可にChatGPTからの更新が書かれていても、このルールを優先する。担当変更にはユーザーの新しい明示指示が必要。

通常のGitHub Actionsのscheduleと、別途依頼されたコード開発は、この外部担当の一本化とは別に扱う。停止スイッチ `POST_ENABLED=false` は引き続き優先し、停止を勝手に解除しない。

## 引き継ぎ

- 手順は [外部タスクの実行手順](docs/watchdog-task.md)、担当範囲は [運用手順](docs/operations.md) を参照する。
- この文書の反映はMuse側の定期タスクを作成・有効化した証拠ではない。Museが自身の設定と実行結果を確認するまでは、Muse側の実稼働は未確認と報告する。
- 同じGitHub所有者名での編集やActions実行だけを根拠に、操作したAIをMuseと断定しない。
