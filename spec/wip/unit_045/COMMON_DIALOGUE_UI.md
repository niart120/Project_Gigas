# U06 M1 共通対話UI

## 1. 目的・状態・起点

状態: **wip、製品実装中**。[U06親仕様](../unit_011/GOAL_EXECUTION.md)のM1を担当する。2026-11-13の依頼に従い、[M0](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)で確認した公開APIとQtの契約を通常turnへ結合し、実装・検証・コミットまで行う。

開始時は `codex/u06-m0-goal-design`、M0の未コミット文書だけが残っていた。M0を `79eb6e5` として独立記録した。製品の起点は `ad69559`。U05の入力停止、最新キャッシュ即応、非同期保存、提出済み設定適用の継続を維持する。

## 2. 対象範囲

通常turnの共通Message、質問回答・追加指示・承認、History、現在turnの公開Plan、公開reasoning summary、実itemのactivity、左右配置、reasoning表示設定。M0の選択/コピー/閲覧保持、秘密入力、小画面、IME/DPIの未確認事項を製品で検証する。

## 3. 対象外

Goal操作・目標トグル・Goal仮表示、run寿命変更、memory/guide生成、可搬対話保存、認証方式や次turn設定変更はM2/M3。U07以降、PR作成・merge・pushも行わない。公開通知がない計画・要約・活動を生成しない。

## 4. 前提・関連文書

[AGENTS.md](../../../AGENTS.md)、[実行管理](../../initial/02-runtime.md)、[工程](../../initial/05-implementation.md)、[検証](../../initial/07-verification.md)、[採用API](../../initial/08-sdk-compatibility.md)、[処理順](../../initial/09-control-flows.md)、[UI](../../initial/10-ui-interaction.md)を正本とする。M0の実呼出しは公開形式の成立確認であり、製品結合の成功とは分ける。

現状は質問ごとの入力欄を表示し、質問待ちに通常入力を隠す。履歴は全本文を組み直してQPlainTextEditへ反映する。これを共通入力とitem単位のQTextBrowser更新へ置き換え、旧経路を併存させない。

## 5. 振る舞い仕様

| 入力・状態 | 期待する振る舞い |
|---|---|
| 通常メッセージ・実行中の追加指示 | 共通欄と送信/Ctrl+Enterを使う。待機時はturn/start、対象turnが実行中ならexpectedTurnId付きsteer。明確な受付前に下書きを消さず、拒否・受付不明で自動再送しない |
| 複数質問 | (要求token, question ID)ごとに下書きを保持。送信先選択で共通欄を切替え、選択中要求の必須回答を一つのanswersへ送る。選択肢、自由回答、秘密回答を区別し、質問間・追加指示へ本文を自動転用しない |
| 承認・要求失効 | 許可/拒否/取消しは公開要求の明示ボタンだけ。要求本文と配送状態は履歴に残し、操作部は未解決時だけ表示。AnswerResultの送信証拠とresolved通知を区別する |
| Plan/summary/activity | 採用thread/turn/itemへ対応付ける。Planは全一覧置換、新turnで現在欄を消す。公開summaryだけを表示し、生reasoningは扱わない。activityは公開itemの対象・結果を一項目へ更新する |
| 履歴更新 | リッチテキストをエスケープし、外部資源を無条件に開かない。itemの更新で選択方向・文字境界・閲覧位置を保ち、選択なしの末尾だけ追従する。隠すsummary内の選択は解除し次項目へ移す |
| reasoning表示設定 | 初期オン。既存設定の保存成功後だけ表示へ適用し、受信を止めない。映像・入力の再接続や停止の契機にしない |
| 小画面・入力 | 左に対話、右に映像。Message・承認・中断・送信と判断本文へ到達可能。Enter改行、Ctrl+Enter送信、IME変換中は送信しない |

GoalのないM1では目標トグル/Goal欄/pause-resumeを出さず、通常turnの中断を使う。設定適用中の中断でも、提出済み設定は継続する。

## 6. TDD Test List

一度に一項目をred→greenへ進め、既存試験を改訂・統合し、項目ごとにコミットする。

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | D01 対象turnの公開Plan・reasoning summary・activityを受け取り、旧turn/生reasoningと区別する | new | unit/integration | 既存開始順序試験へPlanを統合。red 4 failed/69 passed→green 73 passed |
| todo | D02 実行中の追加指示をそのturnへ一度だけ配送し、受付結果・拒否・不明を区別する | new | unit/integration | connection/service既存送信・停止試験を改訂 |
| todo | D03 公開履歴・Planをitem単位で描画し、選択・コピー・閲覧位置を維持する | new | integration | test_codex_guiへ代表条件を統合 |
| todo | D04 共通Messageで質問・追加指示・秘密回答を編集し、承認と配送寿命を保つ | regression | integration | test_codex_gui/answer_lifecycleを置換、旧質問別入力を撤去 |
| todo | D05 reasoning表示の設定を保存成功後に適用し、受信・映像・入力へ影響させない | new | unit/integration | 既存設定保存・適用試験へ統合 |
| todo | D06 対話左・映像右で小画面でも操作と本文へ到達できる | new | integration | 既存レイアウト/停止試験、製品画面で確認 |
| todo | D07 共通画面から公開stdio経路で送信・質問・介入・承認を往復できる | new | integration | 既存transport peerへ固定dialogue例を追加し、GUI試験に統合 |

## 7. 設計判断

公開通知を接続部で型付き値へ変換し、UIがwire全体や資格情報を保持しない。Qt標準部品、既存要求寿命、既存設定保存を再利用する。独自の配送キュー・自動再送・継続ループ・表示DB・保持上限は追加しない。構造整理は各項目のgreen後に判断する。

## 8. 対象ファイル

runtime/codexの公開イベントと通常配送、services/codex、uiのconnection_panel/transcript/request_card/main_window/settings_dialog、storage/settingsとapp/shell、既存unit/integration、利用者docs、親・初期設計。本仕様以外の未着手子仕様は作らない。

## 9. 検証計画・結果

対象pytestのred/green、全unit/integration、ruff format/check、ty、uv lock --check、uv build、git diff --check、文書参照を確認する。製品Qt画面は小画面・大文字、公開通知表示、質問・承認・中断、IME/選択/コピー/スクロールを確認する。機器I/Oを変更しないためM1の実機入力は不要。実runtimeの通常turn結合と固定通知の試験は分けて記録する。

| 項目 | command / 結果 | 確認範囲 |
|---|---|---|
| 開始 | `uv sync --dev` / `uv lock --check`: pass | 採用lockと環境の同期 |
| D01 | `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-u06-runtime`: red 4 failed/69 passed→green 73 passed | 公開イベント欠落とPlan無効のred。thread/turn、summary置換、MCP画像等の非公開対象除外を確認 |
| D01回帰・静的確認 | 接続unit＋サービスintegration 112 passed、全体`ty check --no-progress`、担当ruff check/format、diff check: pass | 型境界とdocstring、書式。追加の構造整理は不要 |

全体gate・製品画面は各項目の結合後に実施する。

## 10. 先送り事項

Goalと入力run寿命・記憶・可搬記録はM2。新規ChatGPT/APIキー・次turn設定・Goal/実機一連受入はM3。長時間履歴/運転はU10。実機が必要になった場合はUSB Video Device、COM8・CH552・9600 baudを使う。自動Qt通知だけで実Windows IMEや実DPI操作の確認済みとはしない。

## 11. 完了チェックリスト

- [x] M0の決定と今回の対象範囲を確認した
- [ ] TDD項目を検証・コミットした
- [ ] 公開型/docstring/docsと品質gateを確認した
- [ ] 製品画面・未検証事項とM2への引継ぎを記録した
- [ ] 最終KISSレビューを行い、本子仕様だけをcompleteへ移した
