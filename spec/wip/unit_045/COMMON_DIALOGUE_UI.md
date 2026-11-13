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

開始時は質問ごとの入力欄を表示し、質問待ちに通常入力を隠していた。履歴は全本文を組み直してQPlainTextEditへ反映していた。これを共通入力とitem単位のQTextBrowser更新へ置き換え、旧経路を併存させない。

## 5. 振る舞い仕様

| 入力・状態 | 期待する振る舞い |
|---|---|
| 通常メッセージ・実行中の追加指示 | 共通欄と送信/Ctrl+Enterを使う。待機時はturn/start、対象turnが実行中ならexpectedTurnId付きsteer。明確な受付前に下書きを消さず、拒否・受付不明で自動再送しない |
| 複数質問 | (要求token, question ID)ごとに下書きを保持。送信先選択で共通欄を切替え、選択中要求の必須回答を一つのanswersへ送る。選択肢、自由回答、秘密回答を区別し、質問間・追加指示へ本文を自動転用しない。送信結果で原稿を消すときは提出本文と送信先キーを照合し、別送信先の同文原稿を消さない |
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
| refactor-skipped | D01 対象turnの公開Plan・reasoning summary・activityを受け取り、旧turn/生reasoningと区別する | new | unit/integration | `0db14a4`。既存開始順序試験へPlanを統合。red 4 failed/69 passed→green 73 passed |
| refactor-skipped | D02 実行中の追加指示をそのturnへ一度だけ配送し、受付結果・拒否・不明を区別する | new | unit/integration | `6cfc3c9`。接続87、サービス/終了回帰149、UI/要求寿命/停止27 passed。受付前消去・実行中送信不可を改訂 |
| refactor-skipped | D03 公開履歴・Planをitem単位で描画し、選択・コピー・閲覧位置を維持する | new | integration | `e2a0148`。GUI 24、関連回帰37 passed。既存test_codex_guiへ代表条件を統合 |
| refactor-skipped | D04 共通Messageで質問・追加指示・秘密回答を編集し、承認と配送寿命を保つ | regression | integration | `50db697`。red 18 failed/18 passed→関連回帰41 passed。test_codex_gui/answer_lifecycleを置換、旧質問別入力を撤去 |
| refactor-skipped | D05 reasoning表示の設定を保存成功後に適用し、受信・映像・入力へ影響させない | new | unit/integration | `aaa66d0`。red 38 failed/110 passed→green 148 passed。既存設定保存・適用試験へ統合 |
| refactor-skipped | D06 対話左・映像右で小画面でも操作と本文へ到達できる | new | integration | `857cca8`。window_presets red 2 failed/4 passed→green 6 passed。関連回帰48 passed、製品描画確認 |
| refactor-skipped | D07 共通画面から公開stdio経路で送信・質問・介入・承認を往復できる | new | integration | `4951e3a`。red 1 failed→green、GUI/transport/要求寿命63 passed。既存peerとGUI試験へ統合 |
| refactor-skipped | D08 Plan表示等で履歴領域が変わっても、末尾追従と過去閲覧を区別する | regression | integration | red 1 failed/36 passed→green 37 passed。関連回帰57 passed。実runtime画像から見つけたviewport縮小後の追従離脱を修正 |

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
| D02 | 接続unit: red 16 failed/70 passed→green 87 passed。サービスの受付選択試験: red 12 failed。接続＋service＋shutdown回帰149 passed。UI＋answer_lifecycle＋ui_control_lifetime: red 2 failed→green 27 passed | `--basetemp=tmp/pytest-u06-runtime`と`tmp/pytest-u06-ui`を使用。送信受付、拒否/不明、取消し・旧世代、質問中steerで入力停止維持、再MCPなし、下書き保持を確認 |
| D02静的・独立レビュー | 全体ty、担当ruff check/format、diff check: pass。独立UIレビュー指摘2条件を既存試験へ統合 | 中断後の遅い通知で配送を再開しないことと、新turn IDへの明示送信。追加の構造整理は不要 |
| D03 | `uv run pytest tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-u06-qt`: red 4 failed/19 passed→green 23 passed。Plan/閲覧端の追加red 2 failed/22 passed→最終24 passed。要求寿命・停止・shutdownとの回帰37 passed | UTF-16逆向き選択、標準コピー、既存item更新、選択対象内置換、末尾/過去閲覧、非表示summaryと再表示、Plan全一覧の選択保持 |
| D03レビュー・描画 | 担当ruff check/format、全体ty、diff check: pass。`tmp/u06/qt/d03_view.py`の合成イベント画像を目視 | Planの位置・チェック/印太字/箇条書き、literal HTML、ユーザー背景、要約字下げ。Plan全置換による選択消失とsummary再表示の41pxずれを採用指摘として修正。描画共有は必要な挙動変更の範囲で、追加refactorなし |
| D04 | `uv run pytest tests/integration/test_codex_gui.py tests/integration/test_answer_lifecycle.py tests/integration/test_ui_control_lifetime.py -q --basetemp=tmp/pytest-u06-composer`: 最終41 passed。担当ruff check/format、全体ty、diff check: pass | 要求/質問別原稿、秘密提出/失効消去、自由回答不可の明示選択、別送信先への遅い受付、配送結果と失効の順序を確認。不要なWrappedRadioを撤去 |
| D04独立レビュー | IMEの2件を既存試験へ統合: red 2 failed/1 passed→green | 要求失効時の本文置換後にcompositionが残る問題と、未受付時に開始した新preeditが受付で消える問題を修正。その他の指摘なし。標準Passwordと共通原稿へ置換した範囲以外のrefactorなし |
| D05 | `uv run pytest tests/unit/test_settings.py tests/unit/test_video_settings.py tests/integration/test_app_settings.py tests/integration/test_settings_apply_contract.py tests/integration/test_settings_recovery.py -q --tb=short --basetemp=tmp/pytest-u06-reasoning`: red 38 failed/110 passed→green 148 passed。担当ruff check/format、全体ty、diff check: pass | 既定値・厳密bool・往復/再起動・取消し3経路・保存待ち/失敗・閉じた後の中断でも提出済み適用を確認。reasoning単独で停止中/取得中のcapture/input再接続なし。既存bool検証を共用し、追加refactorなし |
| D06 | `uv run pytest tests/integration/test_window_presets.py tests/integration/test_codex_gui.py tests/integration/test_answer_lifecycle.py tests/integration/test_ui_control_lifetime.py tests/integration/test_video_gui.py -q --basetemp=tmp/pytest-u06-layout`: 48 passed。担当ruff check/format、全体ty、diff check: pass | 左右配置・status/preset追随、長い送信先/選択肢、承認scrollと中断/送信を確認。800×600/Meiryo UI16ptの製品offscreen描画でHistory101px/Message64px。重複wrapper/見出し/余白とPlan末尾空行を必要な範囲だけ除去、追加refactorなし |
| D07 | `uv run pytest tests/integration/test_codex_gui.py tests/integration/test_codex_transport.py tests/integration/test_answer_lifecycle.py -q --basetemp=tmp/pytest-u06-stdio`: red 1 failed→最終63 passed。担当ruff check/format、全体ty、diff check: pass | ApplicationShell→Service/QThread→stdio→公開decoder/UIを往復。Plan/summary/activity、質問中steer、選択・自由文・秘密の一括回答、承認拒否、終端/close。peerは固定フローのassertだけでRPCを保存しない。追加refactorなし |
| D08 | `uv run pytest tests/integration/test_codex_gui.py tests/integration/test_window_presets.py tests/integration/test_answer_lifecycle.py tests/integration/test_ui_control_lifetime.py tests/integration/test_video_gui.py tests/integration/test_codex_shutdown.py -q --basetemp=tmp/pytest-u06-qt`: 57 passed。担当ruff check/format、全体ty、diff check: pass | Plan show/hideでの末尾/過去閲覧/逆向き選択を既存GUI試験へ統合。Transcriptのresize前後で既存の末尾/可視文字の復元を行い、新しいstate/timerを追加しない。最小再現も失敗→成功 |

全体gate・製品画面は各項目の結合後に実施する。

## 10. 先送り事項

Goalと入力run寿命・記憶・可搬記録はM2。新規ChatGPT/APIキー・次turn設定・Goal/実機一連受入はM3。長時間履歴/運転はU10。実機が必要になった場合はUSB Video Device、COM8・CH552・9600 baudを使う。自動Qt通知だけで実Windows IMEや実DPI操作の確認済みとはしない。

## 11. 完了チェックリスト

- [x] M0の決定と今回の対象範囲を確認した
- [ ] TDD項目を検証・コミットした
- [ ] 公開型/docstring/docsと品質gateを確認した
- [ ] 製品画面・未検証事項とM2への引継ぎを記録した
- [ ] 最終KISSレビューを行い、本子仕様だけをcompleteへ移した
