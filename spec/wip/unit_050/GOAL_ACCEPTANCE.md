# U06 M3 認証・利用制限と工程受入

## 1. 目的・状態・起点

2026-11-13のM3実装・検証依頼に着手。起点cbb0bee、branch `codex/u06-m0-goal-design`、開始時clean。M0〜M2の決定を維持し、残る接続機能と実モデル・実機の受入を完了させる。現時点は調査中、実装・実機受入は未完了。

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
| todo | D02 アカウント・利用制限の取得と通知、取得不能と続行不能の区別 | new/edge、unit/integration | connection通知・service停止・GUI表示へ統合 |
| todo | D03 初回sandbox準備と新規ChatGPTログインの開始・成功・失敗・取消し | edge、unit/integration/実接続 | 既存login/launch/終了経路を優先。調査で必要と確認した差分だけ実装 |

| green | D04 実MCP通知のJSON包みに画像を重複表示・保存しない | regression、unit/実記録replay | 既存公開進捗試験へ実形式を追加。新機能より先に修正 |

## 7. 設計メモ

状態の正本は既存接続と公開Goal。新しい接続管理層・監視・配送キューは作らない。選択と認証の独立に必要な最小の値・通知を追加する。実機のゲーム成否と入力送出、模擬通知の成功と実通知の成功を区別する。

採用版schema（`tmp/u06/storage-auth/schema/v2`）でaccount/read、rateLimits/readと部分更新通知、windowsSandbox/readiness・setupStart・setupCompletedを確認した。[公式app-server文書](https://learn.chatgpt.com/docs/app-server)も2026-11-13に参照。利用枠のnull値から回復を推測しない。sandbox準備は受付と完了通知を分け、書込範囲の最終確認は従来のthread応答検証を維持する。これらのschema確認だけでは製品接続の受入済みとしない。

D01は新phaseを作らず、ModelSelectionの候補を保持して無効なmodel/effortだけ空にする。選び直しは既存select_model、catalogのRPC拒否は接続を維持して明示再接続で再取得する。プロトコル不正・transport障害は従来どおり終了する。

## 8. 対象ファイル

`runtime/codex`、必要な`services`・`ui`・`app`、既存`tests/unit`・`tests/integration`、利用手順docs、親・初期設計。本仕様がM3の検証結果を所有する。

## 9. 検証計画と結果

単体/結合を変更範囲ごとに実行し、最終にruff format/check、ty、unit/integration、lock check、build、配布物検査、diff checkを実施する。公開型/docstringは対応skillで確認。全体gate・配布物はまだnot run。

D01: connectionの旧挙動で5件、GUIの暗黙選択/再開許可で3件、診断保持で2件の期待したredを確認。`uv run pytest tests/unit/test_codex_connection.py tests/unit/test_message_composer.py tests/integration/test_codex_gui.py tests/integration/test_codex_service.py tests/integration/test_codex_transport.py tests/integration/test_window_presets.py tests/integration/test_app_settings.py tests/integration/test_ui_control_lifetime.py -q --basetemp=tmp/pytest-u050-d01-final`は285 passed。全体ruff format/check、ty、lock check、diff check成功。文書validatorは912 links/50 units/errorsなし。型追加・ignore/castなし。公開open/set_modelの説明とdocsを照合。新抽象化は不要と判断しrefactorを省略する。

`uv run python -X utf8 tmp/u06-m3/api_preflight.py`: ホスト制約内では接続失敗。同じコードを制約外で実行すると既存ChatGPT認証、候補8件、windowsSandbox/readiness=ready、account/rateLimits/readの取得と接続終了に成功。`ordinaryUsageAllowed=true`、primary/credits等を受信。証拠は`tmp/u06-m3/api-preflight/report.json`。新規認証・新規home準備・製品利用枠UIの確認ではない。

`uv run python -X utf8 tmp/u06-m3/live_product_probe.py --mode capture`: `capture-20261113-173343-666130/report.json`でUSB Video Device 1920×1080取得、2秒/5秒後のゲーム画面とサービス/worker終了を確認。初回直後は黒、後続はポケモンセンター前の画面。入力・モデルは未要求。待機は検証だけであり製品観測の最新cache即時応答は変更していない。

開始前の製品ライブラリ列挙でUSB Video Device（DSHOW index1）とCOM8を確認。CH552は使用プロトコルでありWindowsのポート表示名はUSB-SERIAL CH340。日本語キーボードレイアウトは存在するが実IME操作は未確認。Qtの実画面列挙はAW2725QF一台、2560×1440 logical、DPR1.5。複数モニター間のDPI遷移は現構成では実行できず、ユーザーが今回の確認自体を対象外とした。別倍率の固定描画で代替済みとしない。

| 受入項目 | 必要な証拠 | 現状 |
|---|---|---|
| 無効設定・認証分離、新規ログイン | 固定RPC/Qtと新規ブラウザ認証の実接続 | 未確認 |
| sandbox初回準備 | 未準備homeから準備、memory書込と原記録保全 | 未確認 |
| 利用制限 | 実取得、固定通知/失敗の停止確認。意図的課金超過なし | 未確認 |
| Goal開始・目的更新・複数turn・完了・pause/resume/clear | 実モデル・実機の通知と操作記録 | 未確認 |
| 介入・質問・承認・配送競合 | 実要求と回答、固定競合試験、遅い応答の失効 | 未確認 |
| 操作権・観測・停止・再公開 | USB Video Device、COM8・CH552・9600 baud。再開初回observe、旧権限失効、入力解除 | 未確認 |
| 次turnモデル/推論レベル、機器設定と停止 | 通常/自律turn、現在turn不変、提出済み機器設定適用の継続 | 未確認 |
| 記憶書込中断・公開記録 | 実機停止と書込停止、再開、対話/input/observeのID対応と非同期保存 | 未確認 |
| 実応答UI | 実summary/Plan/応答、選択コピー/更新中スクロール、実IME（モニター間DPI遷移は対象外） | 未確認 |
| 実接続終了 | 切断/アプリ終了、所有処理回収。M2中間版の終了timeoutを再確認 | 未確認 |

## 10. 先送り事項

APIキーは今回対象外であり再採用時に別途合意する。公開一覧外の背景子孫の終了保証、計画終了・復旧・記憶整理・長時間は親のU07以降の境界を維持する。今回の受入が実行できない場合は理由と影響をここに残し、完了へ移さない。

## 11. チェックリスト

- [x] 合意範囲とAPIキー除外を記録した
- [ ] D01〜D03の必要な実装と既存試験改訂を完了した
- [ ] 全受入項目の実行結果と残る制約を記録した
- [ ] 品質gate、型/docstring・公開docs・初期設計・親仕様を整合した
- [ ] KISSと既存実装の一貫性を再レビューし、論理単位でコミットした

M3実機途中結果: `goal-20261113-173702-808589/report.json`。実gpt-6.1-sol/low、2自律turn・同run、LEFT100ms/RIGHT100ms各1回、各input_sent/neutral=sent、入力前observe、記録ID、公開Plan3件、Goal complete、サービス・所有PID終了を確認。summary0件、current.md更新未確認。モデルがyielded execのwaitをせずturnを終了した記録があり、権限不備とは断定しない。complete通知直後にprobeがinterruptしたため自然最終応答の受入とは分ける。

D04: 実通知のtext blockにMCP結果全体のJSONが入り、activity/detailsへ約104万文字のbase64画像等を重複保存した。採用版で観測した一段のMCP結果包みだけを解き、内側のtext blockを抽出して画像・structuredContent・_metaを除外する。既存test_public_progressを直形式/包み形式でparameterizeし、包み形式1 failed→2 passed（`--basetemp=tmp/pytest-u050-mcp-red`/`-green`）。公開表示とDialogueEventは同じActivityEventを使うため両方へ反映する。

D04実記録replay: `tmp/u06-m3/activity-replay-20261113-174341/report.json`。実6件を公開通知→ActivityEvent/DialogueEvent→製品サービス→RecordingService→ProjectWriterへ通し、約104万文字/件からobserve260文字/act326文字へ縮小。画像base64なし、公開textのID/時刻/寸法は維持、元記録SHA256不変。実際の切詰めは画像dataの内側で外側JSONは有効だった。未観測の壊れたJSONを推測で復元する処理は加えない。対象ruff/ty成功。
