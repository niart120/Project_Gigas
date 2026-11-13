# U06 M3 認証・利用制限と工程受入

## 1. 目的・状態・起点

2026-11-13のM3実装・検証依頼に着手。起点cbb0bee、branch `codex/u06-m0-goal-design`、開始時clean。M0〜M2の決定を維持し、残る接続機能と実モデル・実機の受入を完了させる。D01〜D04の実装・固定検証・コミット済み（D03: 4c021cf）。最終受入監査で見つかったD05を修正中。実接続受入を継続中で、M3全体は未完了。

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
| refactor-skipped | D03 初回sandbox準備と新規ChatGPTログインの開始・成功・失敗・取消し | edge、unit/integration/実接続 | 公開readiness/setupと既存login/終了経路を結合。全832件成功、実新規ログイン・OS準備は本人操作待ち |
| refactor-skipped | D04 実MCP通知のJSON包みに画像を重複表示・保存しない | regression、unit/実記録replay | 既存公開進捗試験へ実形式を追加。859197bで新機能より先に修正 |
| red | D05 Goal complete後の入力を閉じながら最終応答を中断しない | regression、unit/integration/実接続 | 固定peerで製品自身のinterruptによる最終応答欠落を再現。明示停止と自然完了を既存回収試験で区別する |
| todo | D06 Windows準備の公開失敗理由を診断へ保持する | regression、unit/実接続 | 実setup失敗時の理由を既存bool通知変換が破棄していた。既存sandbox試験を改訂し、理由あり/なしを区別する |

## 7. 設計メモ

状態の正本は既存接続と公開Goal。新しい接続管理層・監視・配送キューは作らない。選択と認証の独立に必要な最小の値・通知を追加する。実機のゲーム成否と入力送出、模擬通知の成功と実通知の成功を区別する。

採用版schema（`tmp/u06/storage-auth/schema/v2`）でaccount/read、rateLimits/readと部分更新通知、windowsSandbox/readiness・setupStart・setupCompletedを確認した。[公式app-server文書](https://learn.chatgpt.com/docs/app-server)も2026-11-13に参照。利用枠のnull値から回復を推測しない。sandbox準備は受付と完了通知を分け、書込範囲の最終確認は従来のthread応答検証を維持する。これらのschema確認だけでは製品接続の受入済みとしない。

D01は新phaseを作らず、ModelSelectionの候補を保持して無効なmodel/effortだけ空にする。選び直しは既存select_model、catalogのRPC拒否は接続を維持して明示再接続で再取得する。プロトコル不正・transport障害は従来どおり終了する。

D05の決定: Goal completeでは入力・追加指示・要求を閉じて公開端末を回収するが、現在turnへinterruptを送らず最終応答と自然終端を待つ。turn終端でも公開端末が残っていないことを確認してからstoppedにする。待機中の明示stop/clear、pause・block・制限・失敗では従来どおりinterruptを送る。新phaseや継続ループは作らず、公開一覧外の背景子孫の終了保証は拡張しない。

## 8. 対象ファイル

`runtime/codex`、必要な`services`・`ui`・`app`、既存`tests/unit`・`tests/integration`、利用手順docs、親・初期設計。本仕様がM3の検証結果を所有する。

## 9. 検証計画と結果

単体/結合を変更範囲ごとに実行し、最終にruff format/check、ty、unit/integration、lock check、build、配布物検査、diff checkを実施する。公開型/docstringは対応skillで確認。D03最終変更後も全体gate・配布物検査は成功。実接続受入の完了は別途判定する。

D01: connectionの旧挙動で5件、GUIの暗黙選択/再開許可で3件、診断保持で2件の期待したredを確認。`uv run pytest tests/unit/test_codex_connection.py tests/unit/test_message_composer.py tests/integration/test_codex_gui.py tests/integration/test_codex_service.py tests/integration/test_codex_transport.py tests/integration/test_window_presets.py tests/integration/test_app_settings.py tests/integration/test_ui_control_lifetime.py -q --basetemp=tmp/pytest-u050-d01-final`は285 passed。全体ruff format/check、ty、lock check、diff check成功。文書validatorは912 links/50 units/errorsなし。型追加・ignore/castなし。公開open/set_modelの説明とdocsを照合。新抽象化は不要と判断しrefactorを省略する。

`uv run python -X utf8 tmp/u06-m3/api_preflight.py`: ホスト制約内では接続失敗。同じコードを制約外で実行すると既存ChatGPT認証、候補8件、windowsSandbox/readiness=ready、account/rateLimits/readの取得と接続終了に成功。`ordinaryUsageAllowed=true`、primary/credits等を受信。証拠は`tmp/u06-m3/api-preflight/report.json`。新規認証・新規home準備・製品利用枠UIの確認ではない。

`uv run python -X utf8 tmp/u06-m3/live_product_probe.py --mode capture`: `capture-20261113-173343-666130/report.json`でUSB Video Device 1920×1080取得、2秒/5秒後のゲーム画面とサービス/worker終了を確認。初回直後は黒、後続はポケモンセンター前の画面。入力・モデルは未要求。待機は検証だけであり製品観測の最新cache即時応答は変更していない。

開始前の製品ライブラリ列挙でUSB Video Device（DSHOW index1）とCOM8を確認。CH552は使用プロトコルでありWindowsのポート表示名はUSB-SERIAL CH340。日本語キーボードレイアウトは存在するが実IME操作は未確認。Qtの実画面列挙はAW2725QF一台、2560×1440 logical、DPR1.5。複数モニター間のDPI遷移は現構成では実行できず、ユーザーが今回の確認自体を対象外とした。別倍率の固定描画で代替済みとしない。

| 受入項目 | 必要な証拠 | 現状 |
|---|---|---|
| 無効設定・認証分離、新規ログイン | 固定RPC/Qtと新規ブラウザ認証の実接続 | 無効値/候補/既存認証を確認。本人の新規ログイン成功も公開再取得で確認済み |
| sandbox初回準備 | 未準備homeから準備、memory書込と原記録保全 | 固定受付/完了/失敗/切断を確認。本人の準備操作は実行済みだが実runtimeが失敗を通知。原因調査中 |
| 利用制限 | 実取得、固定通知/失敗の停止確認。意図的課金超過なし | 実アカウント/利用枠UI、固定通知による利用不能停止を確認済み |
| Goal開始・目的更新・複数turn・完了・pause/resume/clear | 実モデル・実機の通知と操作記録 | 実2自律turnとcomplete通知を確認。目的更新/pause/resume/clear・自然最終応答は未確認 |
| 介入・質問・承認・配送競合 | 実要求と回答、固定競合試験、遅い応答の失効 | 固定通知/競合試験は既存実績。今回の実要求は未確認 |
| 操作権・観測・停止・再公開 | USB Video Device、COM8・CH552・9600 baud。再開初回observe、旧権限失効、入力解除 | 同runの初回observe・中立状態の送出を確認。ゲーム機側の解除確認、入力途中停止、paused中会話の入力不可、手動操作後の再開observe/再公開は未確認 |
| 次turnモデル/推論レベル、機器設定と停止 | 通常/自律turn、現在turn不変、提出済み機器設定適用の継続 | M0実API/M2模擬結合を維持。実機設定提出後の停止と適用継続を確認。Goal実行中との結合・製品実モデルの設定確認は準備待ち |
| 記憶書込中断・公開記録 | 実機停止と書込停止、再開、対話/input/observeのID対応と非同期保存 | 実記録ID・画像非複製のreplayを確認。実モデルのmemory書込/中断は未確認 |
| 実応答UI | 実summary/Plan/応答、選択コピー/更新中スクロール、実IME | 実Plan3件、summary未受信。閲覧保持は既存固定試験、実IMEは本人操作とQt未確定/確定イベントで確認済み |
| 実接続終了 | 切断/アプリ終了、所有処理回収。M2中間版の終了timeoutを再確認 | 実Goal/実アカウント/新規home接続のサービス終了と所有PID回収を確認済み |

## 10. 先送り事項

APIキーは今回対象外であり再採用時に別途合意する。公開一覧外の背景子孫の終了保証、計画終了・復旧・記憶整理・長時間は親のU07以降の境界を維持する。今回の受入が実行できない場合は理由と影響をここに残し、完了へ移さない。

2026-11-13の追加指示: 現行実装の検証を終えた段階で、ログイン・実行準備・利用状況を設定ダイアログへまとめる方向を検討する。アカウント認証、Windows側の準備、app-server接続の役割と名称を整理し、日常の接続/切断操作との区別を見直す。検証中にはUI配置変更を広げず、未合意の配置を採用済みとしない。

## 11. チェックリスト

- [x] 合意範囲とAPIキー除外を記録した
- [x] D01〜D03の必要な実装と既存試験改訂を完了した
- [ ] 全受入項目の実行結果と残る制約を記録した
- [ ] 品質gate、型/docstring・公開docs・初期設計・親仕様を整合した
- [ ] KISSと既存実装の一貫性を再レビューし、論理単位でコミットした

M3実機途中結果: `goal-20261113-173702-808589/report.json`。実gpt-6.1-sol/low、2自律turn・同run、LEFT100ms/RIGHT100ms各1回、各input_sent/neutral=sent、入力前observe、記録ID、公開Plan3件、Goal complete、サービス・所有PID終了を確認。summary0件、current.md更新未確認。モデルがyielded execのwaitをせずturnを終了した記録があり、権限不備とは断定しない。complete通知直後にprobeがinterruptし、製品も同通知でinterruptすることが後続監査の固定peer再生で判明した。最終応答の欠落をprobeだけの原因とせず、D05修正後に自然終端を再確認する。

D04: 実通知のtext blockにMCP結果全体のJSONが入り、activity/detailsへ約104万文字のbase64画像等を重複保存した。採用版で観測した一段のMCP結果包みだけを解き、内側のtext blockを抽出して画像・structuredContent・_metaを除外する。既存test_public_progressを直形式/包み形式でparameterizeし、包み形式1 failed→2 passed（`--basetemp=tmp/pytest-u050-mcp-red`/`-green`）。公開表示とDialogueEventは同じActivityEventを使うため両方へ反映する。

D04実記録replay: `tmp/u06-m3/activity-replay-20261113-174341/report.json`。実6件を公開通知→ActivityEvent/DialogueEvent→製品サービス→RecordingService→ProjectWriterへ通し、約104万文字/件からobserve260文字/act326文字へ縮小。画像base64なし、公開textのID/時刻/寸法は維持、元記録SHA256不変。実際の切詰めは画像dataの内側で外側JSONは有効だった。未観測の壊れたJSONを推測で復元する処理は加えない。対象ruff/ty成功。

D02: read.ordinaryUsageAllowed=falseと現在turnのwillRetry=falseだけを停止根拠とする。残高ゼロ・使用率・reset時刻で推測しない。疎な更新通知では完全snapshotを再取得し、単一owned taskが処理中の通知を一回分へまとめる。定期poll・再試行はなくcloseで回収する。falseをnull/失敗で解除しない。接続→Codexの非modalポップオーバーは既存Transcriptの選択/コピー/スクロール保持を再利用し、常設表示を増やさない。

D02検証: redは公開イベント/停止処理未実装4 failed、UI入口未実装。`uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u050-d02-full --junitxml=tmp/u06-m3/d02-tests.xml`は812 passed。全体ruff format/check・ty成功。後続描画修正の関連52件も成功。`tmp/u06-m3/account-render/`の通常960×640・800×600、未取得/取得不能/利用不可を目視し、連結改行を修正。`uv run python -X utf8 tmp/u06-m3/account_live.py`は実ChatGPTアカウント識別・利用許可・利用枠1件を製品UIへ表示、接続終了成功（account-live/report.json、popup.png）。実機で利用枠を枯渇させず、停止は固定通知で確認した。

D01時点の`uv build`・`uv run python scripts/check_package.py`も成功。最終製品変更後に配布物gateを再実行する。

D03設計: Windows製品の接続時にreadinessを取得し、未準備/更新必要/取得不能でも接続・認証を維持する。memory書込が必要なthread準備だけreadyを前提にし、read-only利用は変えない。既存Codexメニューの明示準備操作がsetupStart(mode=elevated、cwd省略)を呼ぶ。開始受付とsetupCompleted・再readinessを区別し、待機中も切断可能。切断でOS準備が取消されたとは表示しない。採用版sourceは起動時設定をreadinessへ使用するため、成功後もnotConfiguredなら自動再接続せず再接続必要を通知する。権限モードの自動fallbackなし。

新規home preflight: `tmp/u06-m3/fresh-home-20261113-173855/report.json`で認証コピーなしの隔離homeからlogin_required、モデル候補9件、readiness=notConfigured、接続終了を確認。その後の本人操作による認証・準備結果は下記に記録する。

D03追加レビュー: 初回readiness完了前のmemory送信を未準備と誤判定する競合を通常/Goal×停止有無の4例でred→green。新規問い合わせ・自動再送は加えず、既存の初回取得taskだけを待つ。停止が先行すればthread/Goalを提出しない。setup完了待ちとcloseの競合も再現し、閉接続へ遅い成功を通知しない。OS処理の取消しを保証する変更ではない。

追加実モデル検証は`ordinary-20261113-180115-976985/report.json`でsandbox準備判定により送信前停止（turn0、入力0、所有PID回収）。`readiness-diagnosis/report.json`で初回取得完了後もupdateRequiredと確認した。前のready応答や既存Goalの成功から現在も準備済みとは推測しない。取得前競合とは別の実状態として、公開setupによる準備と再接続を確認してから残るモデル検証へ進む。

D03最終gate: `uv run pytest tests/unit tests/integration -q`は832 passed、失敗・error・skipなし（JUnit `tmp/u050-d03-reviewed-all.xml`）。`uv run ruff format --check .`（211 files）、`uv run ruff check .`、`uv run ty check --no-progress`、`uv lock --check`、`uv build`、`uv run python scripts/check_package.py`、`git diff --check`が成功。配布物は隔離wheel install、capture worker終了、module/script両入口の起動終了を確認した。公開型/docstring、既存phase・owned task・終了経路をレビュー済み。close競合1例・初回readiness競合4例の指摘を採用して既存試験を改訂し、別状態機械・自動再試行は不要と判断した。

文書確認: 公開手順、初期設計、親/子のAPIキー・DPI除外、記憶と公開記録の所有、次turn設定反映を実装と照合。`uv run python tmp/u06-m1/validate_docs.py`は920 local links、50 unique units、errorsなし。検証用probe・記録は`tmp/u06-m3/`に隔離し、製品へ含めない。

実機設定/停止: `tmp/u06-m3/settings-stop-20261113-180709/report.json`。USB Video DeviceとCOM8・CH552・9600への設定保存を提出直後にstopし、stop完了後もserial接続・映像適用が完了した。続けて取得要求FPS60→30の保存提出直後にstopしても、映像世代1→2へ再接続・適用が完了した。実FPSの測定ではない。同値serialは同ownerを保持し、再接続した証拠にはしない。ボタン入力要求0・モデル0、接続時の中立状態送信のみ。serial owner、全サービスと映像worker PID33184/58844の回収を確認。Goal active中の検証とは区別する。

本人操作の受入: `tmp/u06-m3/fresh-20261113-180214-566284/report.json`。認証コピーなしの隔離homeで、ログイン開始→ブラウザ認証→account再取得authenticated=true→readyを確認。setupは本人の操作後に失敗通知となったため成功扱いにしない。全サービス・所有PIDを正常回収。`fresh-20261113-181828-460469/report.json`では、本人のIME入力・変換・確定に対応するQt InputMethodイベント14件（未確定文字と4文字/3文字の確定）を受信し、モデル送信0で正常終了した。先行の認証用画面は検証fixtureでMessageを無効化しており、IME受入には用いない。

最終受入監査: 実記録74件のevent ID一意、6観測のPNG存在/寸法、2入力完了から観測への参照を独立確認。実モデル設定probeは選択受理だけでは不十分なため、診断のthread/turn IDと実採用model/effortを照合し、診断欠落・不一致では成功にしない。製品には内部診断読取への依存を追加しない。
