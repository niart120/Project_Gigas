# U06 M0 Goal・途中介入・記録・実行設定の設計

2026-11-13の後続合意: 本書の検証実績は当時の範囲を保持する。現在の後続範囲は[U06親](../../complete/unit_011/GOAL_EXECUTION.md)を正とし、APIキー認証とモニター間DPI遷移はM3の対象外。次turn設定はM1、Goal結合はM2で完了済み。

## 1. 目的・状態・起点

状態: **complete、M0調査・設計完了**。[U06親仕様](../../complete/unit_011/GOAL_EXECUTION.md)のM0のみを扱う。2026-11-13の依頼に従い、採用版公開APIと実呼出し、現コード、Qt表示を照合し、最終KISSレビューを完了した。製品実装・Goalの製品結合・実機受入は未着手。

開始時点は `main`、`ad69559`、clean。作業ブランチは `codex/u06-m0-goal-design`。依存はCodex runtime 0.167.0、MCP 2.3.0、PySide6 Essentials 6.12.0、pySerial 3.5。`uv lock --check` は成功。製品コード・テスト・依存は変更していない。

利用場面は、通常会話から目標を開始し、公開計画を読み、回答・追加指示・承認を渡し、入力を止めて同じ会話で明示的に再開すること。

2026-11-13 M1での改訂: 秘密入力の専用UI・消去/保存分岐は用途と実モデル受信の根拠がなく撤回し、`isSecret=true`を含む要求全体を未対応で返す。続く[文脈内対話UI](../../complete/unit_046/CONTEXTUAL_DIALOGUE_UI.md)では送信先選択と独立した承認列を撤去し、右側のPlanを常設する。表示契約は[10](../../initial/10-ui-interaction.md)と後続子仕様を正とし、本書のAPI実測と当時の検証は履歴として残す。

## 2. 対象範囲

Goal開始・目的更新・休止・明示再開・破棄、通常送信と介入、U05操作権とMCP更新、共通Messageと公開表示、guide・memory・対話記録、認証・設定・制限。必要な隔離プローブとQt試作を含む。

## 3. 対象外

製品実装、PR・merge、計画的終了の受入、再起動後の復旧、記憶整理、長時間運転。独自継続ループ、停滞監視、自動復旧、未着手の子仕様は追加しない。

## 4. 前提・関連文書・根拠

[AGENTS.md](../../../AGENTS.md)、[実行管理](../../initial/02-runtime.md)、[ゲームI/O](../../initial/03-game-io.md)、[記憶・保存](../../initial/04-agent-memory.md)、[互換性](../../initial/08-sdk-compatibility.md)、[処理順序](../../initial/09-control-flows.md)、[UI](../../initial/10-ui-interaction.md)に従う。前提は[U02](../../complete/unit_007/CODEX_CONNECTION.md)、[U04](../../complete/unit_009/RECORDED_OBSERVATION.md)、[U05 M2](../../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md)。通常turnの受入をGoalへ拡張しない。

採用実行ファイルの `app-server generate-json-schema --experimental` と公開tag `rust-v0.167.0`（`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`）を照合した。[公式app-server](https://learn.chatgpt.com/docs/app-server)・[Goals](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)は入口の説明に使い、採用版と異なる挙動は実測・固定tagを優先する。

| 根拠 | 今回の確認範囲 | 含まないもの |
|---|---|---|
| 公開schema・固定tag | Goal、steer、要求応答、Plan、reasoning、thread設定、認証・利用枠の形式 | 製品結合、全モデルの動作 |
| 実ChatGPT認証・実モデル | Goal継続、保持再開、pause/clearとinterrupt、目的更新、通常/自律turnのモデル・推論量変更、paused中会話、限定memory書込 | 新規ログイン、APIキー、長時間運転 |
| 実runtime・模擬Responses・模擬機器 | 自律turnから製品MCP/入力/観測/保存、停止、同threadのMCP更新、複数質問・steer・Plan通知 | 判断品質、実機、製品のGoal制御 |
| Qt隔離試作 | リッチテキスト、共通入力、選択/コピー/閲覧位置、長い履歴 | 製品結合、実Windows IME、複数DPI |

## 5. 振る舞い仕様

### Goalと通常送信

threadに属する公開Goalを正本とする。独自Goal IDや攻略状態機械を追加せず、依頼の記録ID・入力run・thread/turnを対応付ける。公開状態は `active / paused / blocked / usageLimited / budgetLimited / complete`。clearはGoal不在にする操作。

| 操作・状態 | 経路と結果 |
|---|---|
| Goalなし、目標オフ | 既存の通常送信。run/MCPを用意して `turn/start`、終端でrun終了 |
| Goalなし、目標オン | 永続thread・指示・記録・MCP/run・通知購読を先に用意し、`thread/goal/set(objective, status=active, origin=user)` を一度呼ぶ。これだけでturnが始まるため `turn/start` を重ねない |
| 未完Goalへ目標オン送信 | 同じGoalの目的更新として `goal/set(objective, origin=user)`。状態・予算は省略して維持する。応答/通知後に目的表示を変え、同文のsteerは送らない。active時はruntimeが現在turnへ更新を通知する。paused時は更新だけで再開しない |
| 完了Goalから新目標 | 実行収束後に旧Goalをclearして新規set。履歴を残し、新しい依頼記録にする |
| activeなturnへ目標オフ送信 | `turn/steer(threadId, expectedTurnId, input)`。通常/自律turnとも同じ。受付はゲーム入力の中断やモデルの遵守を意味しない |
| active Goalのturn間 | 下書きを保持し、次のturn開始後に利用者が送る。保留配送キュー、turn/startによる穴埋め、自動再送は作らない |
| paused Goalへの普通メッセージ | 操作権を開かず `turn/start` で会話でき、Goalはpausedを維持する。目標更新・回答・本文の「再開」で入力許可を戻さない。再開ボタンはこの会話turnも収束してから使う |
| blocked/usageLimited/budgetLimited | 停止理由を保持。通常会話の可否は認証・モデルの利用可否に従うが、Goal/入力再開は別の明示操作。課金先・予算は自動変更しない |

採用版では既存Goalのobjective変更でも `createdAt / tokensUsed / timeUsedSeconds` が維持された。最新公式ページのreset説明と異なるため、[採用版Goal API](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/api.rs#L207)と実測を優先し、更新とclear後の新規作成を分ける。トグル操作だけではAPIを呼ばない。

明確な拒否は未配送として下書きを残す。応答前の通信断は受付不明とし再送しない。完了済みturnへのsteerは `-32600: no active turn to steer` となった。次turn/次Goalへ自動転用しない。完了時に未提出の追加指示を配送対象から外すことと、提出済みだが結果不明の指示を区別して記録する。

### 操作権・停止・再開・MCP更新

現製品の `CodexConnection._adopt_turn` はアプリ送信中しかturnを採用せず、`CodexServiceThread._receive` は各readyでrunを終える。M2はこの寿命をGoalへ合わせる。新しい汎用実行管理層は作らない。

Goal開始から停止まで一つの既存run/Grant/endpointを保持し、自律turnを同threadの活動として採用して `InputService.bind_turn` で記録へ対応付ける。turn間は既存suspend/resumeで新入力を保留し、毎turnの新Grant/MCP再接続は行わない。質問・承認、送信中回答、停止意思が残る間は入力を再許可しない。

pause/stopは最初に `InputService.stop()` で旧Grantを即時失効し、入力と観測応答を中断する。その後は、開始済みwriteの回収とneutral送信、ランタイムの停止を独立して進める。ランタイムへはGoalの `set(status=paused, origin=user)` または `clear(origin=user)` を提出し、実行中turnへ `turn/interrupt` を送る。Goal変更に失敗してもturn中断を試み、操作権を閉じたままにする。pause/clear単独では実turnがinProgressのままとなることを確認した。

Goal変更受付、turn終端、入力解除を別の結果として扱う。遅いactive通知・回答で停止意思を消さない。停止中に自律turn開始が届いても同じ停止手順の対象とし、入力を許可しない。停止完了前の再開は禁止。Goalなしでは一時停止は無効、停止で通常turnを中断する。破棄は履歴削除や達成ではない。

明示再開は、旧入力・turn収束、旧endpoint閉鎖、新run/endpoint準備、`thread/unsubscribe`完了、同IDの `thread/resume` への新MCP設定、保持Goal確認を経て、objective省略の `goal/set(status=active, origin=user)` を一度呼ぶ。利用量を保持する。同一接続内のMCP再読込であり、U08の再起動後復旧とは別。

再開前にreaderのstreaming/Frameありを確認し、共通指示でCodexに最初のobserveと画面・メモの照合を求める。画像はMCPで渡す。goal/setには画像引数がなく、保存PNG待ち・要求後の新Frame待機・余分な準備turnを追加しない。これはモデルへの指示であり、観測順と画面の解釈をアプリが確認済みにしない。製品・実機の初回観測順序はM2/M3で受け入れる。

入力設定変更は共通停止でGoalを保持し、旧入力権を失効する。提出済み設定適用は停止と独立して継続する。適用成功だけでは再開せず、次の明示開始/再開で新能力を公開する。観測解像度だけの変更は次の観測へ適用し、Goal/入力接続を止めない。

300秒は1回の同期MCP要求の期限であり、Goal継続時間、tokenBudget、turn終了期限と別。Goalに300秒制限を追加しない。入力結果不明を再送せず、最新キャッシュ即応、入力・符号化・非同期保存の分離、保存失敗で入力を止めないU05契約を維持する。

### Goal・Plan・History・Message

Goalは目的と公開状態、Planは現在turnの公開計画、Historyはユーザー/Codex/要求/公開活動、Messageは送信前原稿を所有する。配置は[10](../../initial/10-ui-interaction.md)。Goal/Planと下書きやmemoryを同期する機構は設けない。

質問原稿は既存の(HumanRequest.token, question ID)で保持し、要求の必須回答が揃った時だけ一つのanswers応答を返す。M1修正では回答を会話内の要求カードで編集し、Messageは通常送信と追加指示に分ける。自由回答可なら回答と指示を含む全文をその回答へ一度送り、本文分割・複数IDへの同文複製はしない。isOther=falseでは提示された選択肢を使う。承認は要求が提示した判断の明示操作だけで返す。turn/Goal/steerは公開応答、質問/承認は既存AnswerResultのtransport書込完了で送信結果を確定する。後者はモデル側の実行成功ではなく、serverRequest/resolvedだけでも配送成功とはしない。

複数質問待ちのsteer受付と質問応答後の指示配送を実runtimeで確認した。要求を回答済みにする操作ではなく、モデルが取り込む時点も保証しない。`serverRequest/resolved`・中断・turn終端で要求を失効し、遅い回答は再送しない。秘密入力指定はM1で未対応要求として扱う方針へ改訂した。

Planは `tools.update_plan.enabled=true` を明示する。既定falseではupdate_planがunsupportedとなり、有効化後に説明文と2項目の `turn/plan/updated` を受信した。thread/turnを照合して一覧全体を置換する。新turn・目的更新の受付で現在欄を空にし、旧Planは履歴へ残す。M1修正では空でも領域を残し、中断・終了後の最後の計画を状態とともに保持する。未完項目を達成へ変えない。通知にGoal改訂IDはなく、目的更新後の同turnのPlanが新目的を反映済みとは保証しない。独自epochで識別できた扱いにはしない。

reasoningは公開summaryのdelta/完了itemだけを表示し、空なら省略する。生のreasoning本文は対象外。今回の短い実モデル呼出しでは公開summary未受信。activityは実itemの種別・tool名・対象・結果に限る。MCP通知は切詰めがあるため画像原本はU04のObservationを使い、通知のbase64から復元しない。旧itemは該当履歴へだけ反映し、現在Planや入力許可を変えない。

標準 `QTextBrowser / QTextCursor / QSplitter` と既存 `PromptEdit` で足り、追加依存/WebEngineは不要。全履歴のsetHtmlを繰り返さずitem単位で追記/更新し、選択と可視位置を保持する。表示元をエスケープし、外部リンク/画像を無条件に開かない。別の永続DBや仮想化層は先行追加しない。

### guide・memory・対話記録

アプリが初回利用時に不足するguide.mdとmemory/current.mdを作り、既存内容は初期化しない。guideは入口、memoryはCodexの現状と知識、原観測・入力・会話記録はアプリ所有。正本は[04](../../initial/04-agent-memory.md)。

CodexのcwdとruntimeWorkspaceRoots=[memory]は記録先のmemory/、sandboxはworkspace-writeとする。sandbox_workspace_writeはwritable_roots=[]（cwdだけ）、network_access=false、exclude_tmpdir_env_var=true、exclude_slash_tmp=trueを明示し、共有homeの余分な書込先を引き継がない。応答のsandbox条件を確認する。guideと原記録は親から読む。on-request/userを維持し、全権限化や自動承認を採用しない。

desktop由来の `CODEX_WINDOWS_REGISTERED_CORE / CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY` を継承するとsandbox内書込が失敗した。検証子プロセスから両変数だけを除くと、既存認証を維持したまま実モデルのmemoryへのapply_patchが追加承認なしに成功し、実bytesが一致した。別の公開command/exec検証でもmemory書込・親原記録読取は成功し、親原記録追記は拒否・原本不変を確認した。M2で独立runtimeの起動環境とsandboxをこの境界へ合わせる。失敗時にsandbox外へ自動再試行しない。

対話保存は既存RecordingService/ProjectWriterとJSONL envelopeを拡張する。最小分類は、送信/応答/質問/承認の公開内容、Planの更新通知/公開活動の最終item、Goal/turn/設定/配送結果の変化。thread/turn/item/要求IDと必要なrun/観測IDを付ける。送信と受付結果を分け、未送信原稿・秘密回答・Bearer・生RPC全体・内部推論を保存しない。本文・公開summary・activityはdelta毎の永続追記をせず完了itemを保存し、中断/切断時の受信済み部分を一度だけ部分結果として記す。Planはitem完了を待たず、turn/plan/updated受信時の全一覧を保存する。

通知受信順で保存を提出し、一つのwriterへ追記する。保存完了を通知処理・入力・観測の条件にせず、失敗は診断へ残す。異常終了時の未保存を保証外とする。原画像を編集せず、公開応答を観測analysisへ埋め戻さない。独自キュー・保持上限、完全イベント再生、記憶整理は追加しない。

### 認証・モデル・推論量・制限

認証とモデル妥当性を分離し、無効な保存モデルでもaccount/read・model/list・ログインを使えるようにする。取得したモデル/effortから選び、黙って代替しない。TOMLの既定値保存と現在会話の次turn選択は別操作。

既存threadの次turn設定は公開experimental `thread/settings/update(model, effort)` と更新通知を使う。`capabilities.experimentalApi=true` が必要。現turnを変える `turn/settings/update` は使わない。通常送信は設定更新受付後に開始する。Goalではruntimeが設定を反映した後のturnへ適用され、GUI選択時点を境界としない。現在turn表示をfuture設定通知で上書きせず、開始時設定と次turn設定を分ける。競合で確定できない値は未確認とし、内部rolloutを製品の取得経路にしない。

実モデルでsol/lowのturn中にterra/mediumへ変更し、次の自律turnへ反映。同threadの通常turnでも継承し、その途中のsol/low変更は次の通常turnへ反映された。調査用rolloutのturn_contextと照合し、模擬Responses宛の実送信JSONでも同turnの後続推論は旧設定、次turnから新設定と確認した。再接続/会話初期化は不要。MCP再読込でも最新の会話内選択を渡し、古い接続optionsで上書きしない。

既存Codex homeの認証を共有し、資格情報はruntimeが保管する。account/readの取得可能なアカウント識別情報を表示し、切断でログアウトしない。共有homeに影響するアプリ内ログアウト/切替はU06へ追加せず、切断後にruntime側で変更して再接続する。APIキーは一時的に `account/login/start(type=apiKey, apiKey=一時入力)` へ渡しTOML/記録へ残さない。新規ブラウザログインとAPIキー実接続はM3の未検証条件。

account/rateLimits/readと更新通知、公開制限状態/エラーを使う。実ChatGPT認証でordinaryUsageAllowed・利用枠・回復時刻の項目を取得した。未提供の残高やAPIキー利用枠を捏造しない。取得失敗だけでは入力を止めず、明示的利用不能・最終失敗で共通停止する。回復時刻の到来で自動再開しない。課金超過を誘発する検証は行っていない。

## 6. TDD Test List・既存試験の扱い

本子仕様は調査・設計であり製品red/greenは対象外。詳細項目は実装子仕様の着手時に作る。

| 現存試験 | 改訂・統合する振る舞い |
|---|---|
| test_codex_gui / test_answer_lifecycle / test_ui_control_lifetime | 共通欄の下書き、質問ID、承認、失効。旧質問別入力欄の期待値を置換 |
| test_codex_connection / test_codex_service / test_codex_transport | steer配送/受付不明、自律turn採用、Goal/turn終端の分離、設定適用。通常turn停止の保証を残す |
| test_input_service / test_game_io_mcp / test_settings_apply_contract | 同Grantの複数turn、旧要求拒否、MCP再公開、提出済み設定適用の継続 |
| test_observation_storage / test_observation_service | 対話/原観測の非同期保存、ID対応、保存失敗分離、既存記憶保持 |

削除済みの閲覧専用19件を一律復活させない。代表的な選択/コピー/追記を既存Qt試験へ統合し、文書の固定文字列一致で意味の検証を代替しない。

## 7. KISSレビュー

通信・要求・停止・保存は通常turnとGoalで共用し、公開Goal、次turn設定、run寿命の差だけを追加する。毎turnの再接続/再grant、独自continue投入、配送キュー、通知とAPI応答を越えた定期照合ループ、停止時の設定取消しは不要。目的更新と同文steerを二重送信しない。

公開experimental設定経路を利用できない環境は、次turn変更を提供不可と示して採用版/要件を再判断する。独自継続ループや暗黙再接続で補わない。自由回答不可の質問は選択肢と追加指示を分け、本文の自動分類で埋めない。

独立レビューの指摘は採用し、要求tokenと質問IDの組、質問回答中の目標トグル無効化、秘密原稿の隔離、API応答と回答書込完了の区別、Planの目的改訂を識別できない限界、sandbox具体値、固定tagの参照先を修正した。最終レビューでは物理入力の解除とランタイム中断が独立して進む停止順序も揃えた。新しい状態や抽象化は追加していない。

## 8. 対象ファイル

本子仕様・Qt証跡、U06親仕様、関連初期設計、移動に伴う参照。隔離調査資材はtmp/u06/。src/、tests/、依存・lockは変更しない。

## 9. 検証結果

| command / 証跡 | 結果・意味 |
|---|---|
| 同梱runtimeのschema生成、固定tag読取 | pass。tmp/u06/runtime-goal/schema/、source/。安定経路とexperimentalを区別 |
| `uv run python tmp/u06/runtime-goal/live_probe.py` / `settings_probe.py` / `paused_message_probe.py` | tmp/u06/runtime-goal/live-20261113-021248（Goal操作）、021441（通常/自律設定）、021808（paused会話）のreport.json。詳細はtmp/u06/runtime-goal/FINDINGS.mdを参照 |
| `uv run python tmp/u06/runtime-goal/settings_wire_probe.py` | pass。wire-20261113-022053/report.json。同turnの後続推論と次turnの設定境界を実送信で確認 |
| `uv run python tmp/u06/probe_goal_mcp.py` | pass。mcp-20261113-021320/report.json。2自律turn入力、pause/interrupt/neutral、旧Grant拒否、同threadの3DS公開更新/再開 |
| `uv run python tmp/u06/probe_interaction.py` | pass。interaction-20261113-021655/report.json。Plan、2質問一括応答、待機中steer、完了後拒否。先行試行のPlan無効を公開設定で解消 |
| `uv run python -X utf8 tmp/u06/storage-auth/probe.py --clean-launch` / `permission_probe.py` | pass。result-clean-launch.json / permission-result.json。既存認証・候補・利用枠、memory書込、原記録読取/書込拒否を確認。先行失敗と承認付き書込は別記録 |
| `uv run --no-sync python -X utf8 tmp/u06/qt/probe.py` | pass。Qt 6.12.0/offscreen/Windows style。2,000件・193,034 UTF-16文字、逆向き選択、日本語/絵文字コピー、追記/以前item更新/reasoning切替の可視位置差0px、末尾追従、Enter/Ctrl+Enter/IME preedit抑止 |
| `uv run python -X utf8 tmp/u06/validate_docs.py` / `git diff --check` | pass。変更Markdown 22件、spec内のローカル参照先810件、unit 44件の一意性、仮テキスト・code fence・製品変更なしを確認。文書の根拠・未検証表示・親子の整合は本文と差分を読み、独立レビューと照合した |
| 全体pytest / ruff / ty / build | not run。製品・依存・テスト基盤未変更。今回の文書/隔離試作を検査するgateではない |
| 実機Goal・製品GUI結合 | not run。M0成立確認は模擬機器で足り、M2/M3の受入に残す |

M0時点のQt画像は[通常](assets/qt-normal.png)、[一時停止後](assets/qt-paused.png)。現在の製品配置の証拠ではない。合成履歴の最終追記5.809秒は性能保証ではない。文字拡大専用の例・画像参照は後続のM1修正合意で撤去し、通常フォントの大小ウィンドウと判断本文の可読性を再検証する。

## 10. 未確認事項・M1着手条件・後続

M1は通常turnのMessage/History/Plan/要求応答を実通知へ結合する範囲。要求寿命と下書きを維持し、Goalの仮表示/偽の成功を置かない。選択対象そのものの更新/非表示、小画面の判断本文を製品で確認する。秘密指定は要求全体の未対応拒否、実Windows IME・複数DPIは後続受入としてM1で整理した。

M2はGoal/Grant寿命、停止と設定適用の独立、memory限定書込/起動環境を製品へ結合する。質問・承認中のGoal完了/制限、開始/再開と停止、旧通知、memory書込中断、自律turn開始と設定変更の競合は未検証。turn終端からバックグラウンドコマンドの全終了を推定せず、共通指示で不要なバックグラウンド処理を使わない。独自の終了監視は追加しない。

M3は既存/新規ChatGPT認証とAPIキー、公開制限エラー、実モデルのGoal達成/休止と短い実機依頼を受け入れる。USB Video Device、COM8・CH552・9600 baudで、初回observe、複数turn、介入、pause/再開/clear、提出済み設定適用、画像/操作/会話IDの対応を確認する。Plan/reasoningが毎turn必ず出ることは要求しない。

U07は区切り・セーブ・終了作業、U08は保存参照からの復旧、U09は記憶整理/復元、U10は長時間・300秒実待機・他機器を所有する。今回の短い成功をその受入へ流用しない。

## 11. 完了チェックリスト

- [x] 前提・Git・対象範囲を確認し親をwipへ移した
- [x] 公開形式と実行結果から契約を確定した
- [x] Qt・記録・認証、既存試験の改訂方針を記録した
- [x] 初期設計・親仕様の整合、最終KISSレビューと文書検査を完了した
