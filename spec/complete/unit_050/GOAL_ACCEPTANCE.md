# U06 M3 認証・利用制限と工程受入

## 1. 目的・状態・起点

2026-11-13にM3を完了した。起点cbb0bee、branch `codex/u06-m0-goal-design`、開始時clean。M0〜M2の決定を維持して接続機能と実モデル・実機を受入検証した。D01〜D04を実装・固定検証・コミット済み（D03: 4c021cf）。監査で見つかったD05は10fa771、D06は204855cで修正した。実受入結果と残る制約は第9章へ記録し、公開docs・親・初期設計との最終整合を終えた。状態はcomplete。

## 2. 対象範囲

認証とモデル妥当性の分離、新規ChatGPTブラウザログイン、アカウント表示、Windows sandbox初回準備、利用制限の取得・通知・表示を扱う。既存のGoal・通常送信・途中介入・質問承認・停止再開・設定・記憶記録・UI・終了を実モデルと指定実機で結合確認する。

## 3. 対象外

ユーザー合意によりAPIキー認証は実装・受入とも今回対象外。アプリ内ログアウト・アカウント切替、文字拡大専用の例・検証、モニター間DPI遷移の確認（追加合意で対象外）、独自継続ループ、自動復旧・課金切替、U07計画終了・U08再起動復旧・U09記憶保守・U10長時間、PR・マージは行わない。MCPの300秒は各呼出しの期限でありGoal継続時間ではない。300秒実待機はU10。

## 4. 関連文書・着手条件

[親](../unit_011/GOAL_EXECUTION.md)、[M0](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)、[M1最終](../../complete/unit_048/NEXT_TURN_MODEL.md)、[M2](../../complete/unit_049/GOAL_MEMORY_INTEGRATION.md)、[実行管理](../../initial/02-runtime.md)、[記憶](../../initial/04-agent-memory.md)、[検証](../../initial/07-verification.md)、[公開API](../../initial/08-sdk-compatibility.md)、[UI](../../initial/10-ui-interaction.md)。M2の793件成功と模擬Responses/機器での実runtime結合を前提とし、実モデル・実機の証拠へ拡張しない。

採用runtime0.167.0の公開schema/sourceと既存実装を調べてから変更する。新規ログインで本人操作が必要な場合と、sandbox準備のOS確認は本人へ引き継ぎ、認証情報を複製しない。

## 5. 振る舞い仕様

保存済みモデル・推論レベルの無効値で接続を破棄しない。取得できた候補から同一接続で選び直せるようにし、有効な選択まで送信を拒否する。未認証でもログイン経路を維持する。暗黙のモデル置換をしない。

アカウントと利用制限は公開APIの返却値だけを表示する。取得不能は不明として扱い、取得失敗だけで入力を停止しない。明示的な続行不能・最終失敗は既存停止へ接続する。429だけで残高不足等を断定せず、回復時刻での自動再開をしない。

通常・自律turnの選択反映は既存実装を維持し、受理後の次turnへ適用して現在turnを変えない。最新cacheを即時返す観測、入力・観測・非同期保存の責務分離、停止しても提出済み設定適用が続くU05契約を維持する。

## 6. TDD Test List

| 状態 | item | 種別・層 | 既存試験への反映 |
|---|---|---|---|
| refactor-skipped | D01 無効既定値でも認証・候補選択ができ、有効選択まで送信しない | regression、unit/integration | connection・GUI・serviceの既存認証/候補試験を改訂。下記285件成功、dadc31fへコミット |
| refactor-skipped | D02 アカウント・利用制限の取得と通知、取得不能と続行不能の区別 | new/edge、unit/integration | connection通知・service停止・GUI表示へ統合、812件成功、96937c9へコミット |
| refactor-skipped | D03 初回sandbox準備と新規ChatGPTログインの開始・成功・失敗・取消し | edge、unit/integration/実接続 | 公開readiness/setupと既存login/終了経路を結合。全832件成功、新規ログインとOS準備後の再接続readyを確認 |
| refactor-skipped | D04 実MCP通知のJSON包みに画像を重複表示・保存しない | regression、unit/実記録replay | 既存公開進捗試験へ実形式を追加。859197bで新機能より先に修正 |
| refactor-skipped | D05 Goal complete後の入力を閉じながら最終応答を中断しない | regression、unit/integration/実接続 | 固定peerの欠落・停止競合・UI試験を改訂して全843件成功。実モデルでもcomplete後の最終応答、自然completed、ready、同区間interrupt 0を確認 |
| refactor-skipped | D06 Windows準備の公開失敗理由を診断へ保持する | regression、unit/実接続 | 既存sandbox失敗試験を理由あり/null/空へ改訂し1 failed→5 passed。全845件成功。実OS準備の再実行は成功 |

## 7. 設計メモ

状態の正本は既存接続と公開Goal。新しい接続管理層・監視・配送キューは作らない。選択と認証の独立に必要な最小の値・通知を追加する。実機のゲーム成否と入力送出、模擬通知の成功と実通知の成功を区別する。

採用版schema（`tmp/u06/storage-auth/schema/v2`）でaccount/read、rateLimits/readと部分更新通知、windowsSandbox/readiness・setupStart・setupCompletedを確認した。[公式app-server文書](https://learn.chatgpt.com/docs/app-server)も2026-11-13に参照。利用枠のnull値から回復を推測しない。sandbox準備は受付と完了通知を分け、書込範囲の最終確認は従来のthread応答検証を維持する。これらのschema確認だけでは製品接続の受入済みとしない。

D01は新phaseを作らず、ModelSelectionの候補を保持して無効なmodel/effortだけ空にする。選び直しは既存select_model、catalogのRPC拒否は接続を維持して明示再接続で再取得する。プロトコル不正・transport障害は従来どおり終了する。

D05の決定: Goal completeでは入力・追加指示・要求を閉じて公開端末を回収するが、現在turnへinterruptを送らず最終応答と自然終端を待つ。turn終端でも公開端末が残っていないことを確認してから既存のreadyに戻り、Planを自然終了として表示する。待機中の明示stop/clear、pause・block・制限・失敗では従来どおりinterruptを送りstoppedとする。先行した明示停止を遅いcomplete通知で解除しない。新phaseや継続ループは作らず、公開一覧外の背景子孫の終了保証は拡張しない。

D05は完了時のturn IDだけを保持する。先行item/要求で別turnのIDが採用される場合があるため、現在turn IDだけでは自然終了を待つ対象を区別できない。手動停止・終端でこの保持値を消し、端末回収と中断応答は既存owned taskで待つ。UIは既存Goal/turn/手動停止の値から中断可否を決め、新しいphaseや状態管理層を増やさない。

D02: read.ordinaryUsageAllowed=falseと現在turnのwillRetry=falseだけを停止根拠とする。残高ゼロ・使用率・reset時刻で推測しない。疎な更新通知では完全snapshotを再取得し、単一owned taskが処理中の通知を一回分へまとめる。定期poll・再試行はなくcloseで回収する。falseをnull/失敗で解除しない。接続→Codexの非modalポップオーバーは既存Transcriptの選択/コピー/スクロール保持を再利用し、常設表示を増やさない。

D03設計: Windows製品の接続時にreadinessを取得し、未準備/更新必要/取得不能でも接続・認証を維持する。memory書込が必要なthread準備だけreadyを前提にし、read-only利用は変えない。既存Codexメニューの明示準備操作がsetupStart(mode=elevated、cwd省略)を呼ぶ。開始受付とsetupCompleted・再readinessを区別し、待機中も切断可能。切断でOS準備が取消されたとは表示しない。採用版sourceは起動時設定をreadinessへ使用するため、成功後もnotConfiguredなら自動再接続せず再接続必要を通知する。権限モードの自動fallbackなし。

D06は公開setupCompleted.errorを既存例外・SandboxEvent.detail・操作結果・通知詳細へ保持する。成功時はerrorを採用せず、失敗理由がnull/空なら既存の短い失敗理由だけを示す。Futureの結果を成功None/失敗文字列で表し、新しいクラス・状態は追加しない。schema、切断・pending境界、公開型/docstringを独立レビュー済み。

## 8. 対象ファイル

`runtime/codex`、必要な`services`・`ui`・`app`、既存`tests/unit`・`tests/integration`、利用手順docs、親・初期設計。本仕様がM3の検証結果を所有する。

## 9. 検証計画と結果

### 自動検証・表示・文書

既存試験を改訂し、次のred→greenを確認した。各段階の件数は当時の回帰範囲であり、最終gateはD06後の845件。

| 対象 | 再現した不具合と確認範囲 | greenの根拠 |
|---|---|---|
| D01 | connection旧挙動5件、GUI暗黙選択/再開許可3件、診断保持2件のred。有効選択まで送信せず認証・候補を維持 | connection/composerとGUI/service/transport/window/settings/lifetimeの既存試験285 passed（`tmp/pytest-u050-d01-final`） |
| D02 | 公開イベント/停止未実装4 failed、UI入口未実装。取得不能と続行不能を区別 | 全812 passed（`tmp/u06-m3/d02-tests.xml`）、描画修正の関連52件も成功 |
| D03 | 初回readiness待ちを通常/Goal×停止有無の4例、setup完了とclose競合1例で再現 | 全832 passed（`tmp/u050-d03-reviewed-all.xml`）。既存初回取得taskのみ待ち、停止先行なら未提出、閉接続へ遅い成功を通知しない |
| D04 | 実MCP結果のJSON包みで画像を重複保存。既存`test_public_progress`の包み形式1 failed | 直形式/包み形式2 passed（`tmp/pytest-u050-mcp-red`/`-green`）。観測済みの一段だけを解き、内側textを残して画像・structuredContent・_metaを除外 |
| D05 | completeの8件で最終応答欠落を再現。中断RPC前の終端、停止先行/遅いcomplete、UI中断不能/Plan誤表示も既存試験へ統合 | 全843 passed、62.55秒（`tmp/u050-d05-final-all.xml`）。実自然完了は下記の別証拠 |
| D06 | 既存`test_sandbox_setup_failure_preserves_connection_and_requires_ready_confirmation`で公開理由喪失1 failed/4 passed | `-k sandbox_setup_failure_preserves_connection --basetemp=tmp/pytest-u050-d06-red`から`-green`へ5 passed。理由あり/null/空と成功後のreadinessを確認 |

D06後の最終gateはすべて成功。pytestは845 passed（61.71秒）。配布物検査は隔離wheel install、capture worker終了、module/script両入口の起動終了を含む。

```powershell
uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u050-d06-all --junitxml=tmp/u050-d06-all.xml
uv run ruff format --check .
uv run ruff check .
uv run ty check --no-progress
uv lock --check
uv build
uv run python scripts/check_package.py
git diff --check
```

公開型/docstring、既存phase・owned task・終了経路、利用手順、初期設計、親/子の対象外・記録所有・次turn設定反映を照合した。完了移動後の`uv run python tmp/u06-m1/validate_docs.py`は921 local links / 50 unique units / errorsなし、`git diff --check`も成功。READMEの旧未実装記述と承認候補の利用手順を現行へ直した。最終整理は文書のみのためPythonテスト・静的検査・buildは再実行せず、上記D06後の製品gateと文書確認を区別する。別状態機械・自動再試行・新しい接続管理層は不要と判断した。probeと実証は`tmp/u06-m3/`に隔離し、製品に含めない。以下の短縮した実証パスも同ディレクトリからの相対位置。

### 実接続・実機の証拠と限界

**環境・利用枠。** `uv run python -X utf8 tmp/u06-m3/api_preflight.py`はホスト制約内で接続失敗、制約外では既存ChatGPT認証、候補8件、当時のreadiness=ready、利用枠と接続終了を確認（`api-preflight/report.json`）。`uv run python -X utf8 tmp/u06-m3/account_live.py`ではアカウント識別・利用許可・利用枠1件を製品UIに表示（`account-live/report.json`、`popup.png`）。`account-render/`の960×640・800×600、未取得/取得不能/利用不可も目視した。実利用枠は枯渇させず、利用不能停止は固定通知の証拠とする。

USB Video Device（DSHOW index1、1920×1080）、COM8・CH552・9600を使用。Windowsポート表示名はUSB-SERIAL CH340。`uv run python -X utf8 tmp/u06-m3/live_product_probe.py --mode capture`で2秒/5秒後の画面とworker終了を確認（`capture-20261113-173343-666130/report.json`）。初回フレームは黒だったため検証時だけ待機した。製品の即時cache応答は変更していない。Qt実画面はAW2725QF一台、2560×1440 logical、DPR1.5。対象外のモニター間DPI遷移を別倍率描画で代替済みとはしない。

**新規認証・準備・IME。** `fresh-home-20261113-173855/report.json`で認証コピーなしのhomeからlogin_required・候補9件・notConfiguredを確認。`fresh-20261113-180214-566284/report.json`では本人のブラウザ認証後、account再取得authenticated=true・readyになった。同homeで最初のsetupは失敗し、その原因は保存ログから復元できない。`post-setup-readiness-20261113-183528/report.json`でも認証保持・notConfiguredを確認した。D06後の本人による再準備は成功（`fresh-20261113-184325-895094/report.json`）。同接続は起動時設定により再接続必要、明示再接続後は同homeで認証保持・readiness=readyとなった（`post-setup-readiness-20261113-185025/report.json`）。各接続は正常終了し、最初の失敗原因を後の成功から推定しない。

既定homeは後にupdateRequiredへ変わり、追加モデル検証を送信前に停止した（`ordinary-20261113-180115-976985/report.json`、`readiness-diagnosis/report.json`、turn/入力0）。最終の`uv run python -X utf8 tmp/u06-m3/readiness_probe.py`でも2つのcwdで認証保持・updateRequired・正常closeを確認した（`readiness-cwd-20261113-191758/report.json`、setup/model/input 0）。今回の準備・実モデル受入成功は`fresh-20261113-180214-566284/home`に限り、既定homeまで更新済みとはしない。既定homeを使う場合は公開setupと明示再接続を行い、readyになるまでmemory付き開始を拒否する。これは残る環境準備の運用条件であり製品機能の未実装ではない。自動setup・認証コピーは行わない。

本人のIMEは`fresh-20261113-181828-460469/report.json`で未確定・変換・確定のQt InputMethodイベント14件を確認し、モデル送信0で終了。Message無効の先行認証fixtureはIME証拠に含めない。

**通常送信・設定・介入。** `uv run python -X utf8 tmp/u06-m3/model_acceptance.py ordinary --codex-home tmp/u06-m3/fresh-20261113-180214-566284/home`で成功（`ordinary-20261113-185104-780025/report.json`）。実質問保留中にsol/low→terra/mediumを選び、回答sentと両turnの最終応答・自然終了を確認。公開turn/startと所有runtimeのturn_contextは現在sol/low・次terra/mediumで一致し、選択受理は両startの間。thread/settings/update・turn/settings/updateは0件。診断のthread/turn IDと実採用設定を照合したが、製品に内部診断読取依存は追加していない。

`ordinary-steer-20261113-190140-132573/report.json`では質問の待機条件を変えないsteerを1回送り、対象turnへのaccepted、独立した回答sent、両markerを含む最終応答・自然completedを確認。承認は`approval-accept-20261113-190231-054999/report.json`で無害なWrite-Outputへacceptを1回返して期待出力・終了コード0・自然終端、`approval-cancel-20261113-190635-130220/report.json`でcancelを1回返してcommand declined/turn interruptedを確認した。取消しへの追加stop・turn/interruptは0。実要求はaccept・永続候補・cancelを提示し、declineは未提供、永続候補も未選択。schemaの型とavailableDecisionsを区別し、未提供の選択を捏造しない。初回probeの同梱shell正規化による安全停止は製品失敗に含めない。これらは機器入力なしで、所有PIDを回収した。

**Goalの進行。** `goal-20261113-173702-808589/report.json`では実sol/lowの2自律turn・同run、LEFT100ms/RIGHT100ms各1回、入力前observe、input_sent/neutral=sent、公開Plan3件とcomplete通知を確認した。74原記録のevent ID一意、6PNGの存在/寸法、2入力完了の観測参照を独立照合。current.md更新は未確認で、yielded execをwaitせずturnが終わっているため権限不備とは断定しない。complete時はprobeだけでなく製品もinterruptしていたため、自然最終応答の証拠はD05後の同時停止検証で取り直した。

`goal-lifecycle-20261113-185221-246532/report.json`では実turn_contextの現在sol/low・次自律terra/medium（回収と競合した第3turnも同値）を確認。後半はprobeの回答とsteerが19ms差で競合し未到達であり、配送失敗とはしない。`goal-lifecycle-20261113-190012-584082/report.json`は目的更新、実質問2回、自律設定反映、pause、paused通常会話、同thread再開受付まで確認したが、終了済みMCP taskを未終了と誤判定して停止した。旧MCP回収は後述の手動再開でtask.done()を確認した。paused会話中の拒否は実質問保留時の空sequenceでINPUT_DISABLED/input_sent=noneを確認した範囲に限り、実押下や会話全期間へ広げない。M2固定試験・実コードのrun権限取消しと区別する。

**手動介入後の再開。** `goal-resume-final-20261113-191328-040151/report.json`では、Goalを質問保留中にpauseし、本人の移動完了返答後に同threadで再開した。`visual-review.json`で手動前後の位置・向きの変化と、再開observeが手動後の状態を示すことを確認。初回ツールは新MCPのobserveで、フレームsequenceは再開直前7013→観測7535。新run/新MCP、旧MCP task.done()、旧GrantのINPUT_DISABLEDを確認した。同じ接続機器で現Grantの空sequenceはINVALID_SEQUENCEとなり、権限の差と入力列の不正を分けて確認した。非中立入力・入力操作記録は0で、実押下停止は上記ではなく下記の同時停止検証が根拠となる。再開turnの最終応答・自然completed・ready後、公開goal/clear成功とGoalなしを確認し、所有PID54828/89560を回収した。

**入力・記憶・設定の同時停止と自然完了。** 次を1回だけ実行し成功した（`combined-stop-20261113-190831/report.json`、画面照合は同ディレクトリ`review.md`）。

```powershell
uv run python -X utf8 tmp/u06-m3/combined_stop_probe.py --execute --codex-home tmp/u06-m3/fresh-20261113-180214-566284/home --direction RIGHT --hold-ms 500 --settings-stop
```

| 確認対象 | 実結果と限界 |
|---|---|
| 書込境界 | 新規homeの実モデルでmemory書込成功、親events.jsonl先頭読取成功。親のunique使捨てファイル作成はUnauthorizedAccessExceptionで拒否。原記録自体へ書き込まず、非同期追記によるhash差を保全根拠に使わない |
| 同時停止 | 有限writer実行中にRIGHTを1回送出、78ms後に中立送出（要求500msの途中）。原記録はinterrupted / completed_steps=0 / input_sent=sent / neutral=sent / phase=input。公開端末terminate=true、停止後一覧は空。writerは135行・565 byteで停止し、1秒後と再開後も不変 |
| 設定適用 | Goal active中に取得FPS要求60→30を提出し直後stop。保存と映像世代1→2の適用は継続し、同値serialのownerを維持。実FPS測定やserial再接続の証拠ではない |
| 再開・自然完了 | 同Goalの新run/新MCPで初回observe、旧writer・入力の再実行なし。complete後にmarker入りassistant replace、実turn/completed=completed、readyを受信。同区間interrupt=0。原記録sequence 51=complete、53=最終応答partial false、55=ready |
| 回収 | 全サービス・serial終了、所有PID7820/39864/69072消滅。検証guardの拒否0回で、guardを製品の旧Grant拒否の証拠にはしない |

独立監査でも同時停止65件・手動再開75件の計140原記録のID一意・sequence/参照整合、4PNGの1920×1080復号と観測ID対応、唯一の入力のinterrupted/sent/neutral、手動再開の製品入力0、5所有PID不在、memory 565 byteの停止保持を確認し、blocking指摘はなかった。原記録への破壊的書込は試しておらず、親の使捨てファイル作成拒否と記録整合を保全根拠とする。

画面は入口正面から右1タイル相当へ移動。再開observe画像（19:09:41.806）とafter-resume.png（19:09:52.974）は約11.17秒差で主人公・背景位置が同じ、NPC姿勢は変化し、右側に障害物はない。入力後の画面停止は確認できるが、78msはPC側の送出間隔であり機器ACK・瞬間の物理解除は未観測。after-stop.pngは映像再設定の第1フレームで黒く、解除根拠にしない。入力・モデルなしの先行設定停止も`settings-stop-20261113-180709/report.json`で確認済み。

**公開記録・表示。** D04の`activity-replay-20261113-174341/report.json`では実6件を公開通知→ActivityEvent/DialogueEvent→サービス→RecordingService→ProjectWriterへ通し、約104万文字/件からobserve260文字/act326文字へ削減。画像base64なし、textのID/時刻/寸法と元記録SHA256を維持した。観測済みの外側JSONは有効で、壊れたJSONの推測復元は加えていない。

実Plan・応答は受信したが、今回の製品設定でsol/low・terra/mediumの短い呼出しから非空summaryは0件（185221のReasoningEvent 5件も空配列）。全モデルの非対応や実summary表示成功とはしない。表示/非表示・選択/コピー・更新中スクロール保持は既存固定通知の試験、実IMEは本人操作の証拠として区別する。

### 受入判定

| 受入項目 | 必要な証拠 | 現状 |
|---|---|---|
| 無効設定・認証分離、新規ログイン | 固定RPC/Qtと新規ブラウザ認証の実接続 | 無効値/候補/既存認証を確認。本人の新規ログイン成功も公開再取得で確認済み |
| sandbox初回準備 | 未準備homeから準備、memory書込と原記録保全 | 本人の再準備成功、再接続ready・認証保持、実モデルmemory書込・親記録読取・親使捨てファイル書込拒否を確認 |
| 利用制限 | 実取得、固定通知/失敗の停止確認。意図的課金超過なし | 実アカウント/利用枠UI、固定通知による利用不能停止を確認済み |
| Goal開始・目的更新・複数turn・完了・pause/resume/clear | 実モデル・実機の通知と操作記録 | 開始/目的更新/複数turn/pause/paused会話/同thread再開・自然最終応答・clearを確認 |
| 介入・質問・承認・配送競合 | 実要求と回答、固定競合試験、遅い応答の失効 | 実質問/回答・steer・一回許可/取消しを確認。実要求で未提供のdeclineは固定試験のみ。配送競合・失効も既存固定試験と区別 |
| 操作権・観測・停止・再公開 | USB Video Device、COM8・CH552・9600 baud。再開初回observe、旧権限失効、入力解除 | 入力途中停止・中立送出・画面停止、本人手動後の初回observe、新run/MCP・旧MCP回収、旧Grant拒否と現Grantの対照、paused質問保留時の空sequence拒否を確認。機器ACKは未観測 |
| 次turnモデル/推論レベル、機器設定と停止 | 通常/自律turn、現在turn不変、提出済み機器設定適用の継続 | 実通常/自律turnの選択反映・現turn不変、Goal active中の機器設定提出後stopと適用継続を確認 |
| 記憶書込中断・公開記録 | 実機停止と書込停止、再開、対話/input/observeのID対応と非同期保存 | 実モデルの有限memory書込を入力と同時停止し、再開後も不変。実記録ID、最終応答保存、画像非複製replayを確認 |
| 実応答UI | 実summaryの提供範囲、Plan/応答、選択コピー/更新中スクロール、実IME | 実Plan/応答を受信、今回の条件で非空summaryは未提供。閲覧保持は既存固定試験、実IMEは本人操作とQt未確定/確定イベントで確認済み |
| 実接続終了 | 切断/アプリ終了、所有処理回収。M2中間版の終了timeoutを再確認 | 実Goal/実アカウント/新規home接続のサービス終了と所有PID回収を確認済み |

受入表の必要な証拠は揃った。非空summaryとdeclineは実要求の提供範囲、機器ACKは入力プロトコルの観測範囲による制約であり、未実行項目や成功の証拠に置き換えない。固定通知で確認したUI/配送契約、実提供された応答・判断、実送出と画面停止を根拠としてM3の受入は満たす。KISS再レビューでは、独自継続・追加状態機械・復旧ループを増やさず、U05の即時cache・責務分離・提出済み設定適用を維持している。公開docs・親・初期設計を整合し、親子をcompleteへ移した。

## 10. 先送り事項

APIキー、モニター間DPI遷移、文字拡大専用の受入は合意済みの対象外。公開一覧外の背景子孫の終了保証、計画終了・復旧・記憶整理・長時間は親のU07以降の境界を維持する。非空summary・未提供decline・機器ACKの制約は第9章に残し、将来のruntime/機器で確認するときも今回確認済みとは扱わない。

2026-11-13の追加指示: 現行実装の検証を終えた段階で、ログイン・実行準備・利用状況を設定ダイアログへまとめる方向を検討する。アカウント認証、Windows側の準備、app-server接続の役割と名称を整理し、日常の接続/切断操作との区別を見直す。検証中にはUI配置変更を広げず、未合意の配置を採用済みとしない。

実画面では準備成功後の「再接続が必要」も共通の「Codex・処理失敗」に分類された。上記整理で、準備失敗と準備後の再接続待ちを区別する。配置変更だけでは解消しない通知分類の論点であり、自動再接続・自動再準備の追加を意味しない。

UI配置・名称と準備後の通知分類は、後続の[設定UI整理](../unit_051/CODEX_SETTINGS_UI.md)で実装・検証した。M3実証の時点と後続変更を区別する。

## 11. チェックリスト

- [x] 合意範囲とAPIキー除外を記録した
- [x] D01〜D06の必要な実装と既存試験改訂を完了した
- [x] 全受入項目の実行結果と残る制約を記録した
- [x] 最終845件と静的検査・配布物検査、公開型/docstringを確認した
- [x] KISSと既存実装の一貫性を再レビューし、実証整理を論理単位にまとめた
- [x] 公開docs・初期設計・親仕様を最終受入結果と整合した
