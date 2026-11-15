# 手動コントローラ 仕様書

## 1. 概要

状態はwip。[unit_078](../unit_078/VIRTUAL_CONTROLLER.md)の段階3。段階1・2のサービス分離を前提に、採用配置の別ウィンドウ、手動状態、既存実行との排他を追加する。

| source | 内容 | path |
|---|---|---|
| 設計合意・実装依頼 | 操作契約と配置の正本 | [親仕様](../unit_078/VIRTUAL_CONTROLLER.md) |
| 段階2完了 | 1033テスト、実Codex・COM8接続・切断・アプリ終了 | [unit_080](../../complete/unit_080/INPUT_CONFIGURATION_OWNERSHIP.md) |
| NyXPy読取 | ボタン・円内スティック・8方向padを参考にする | [pane](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/gui/panes/virtual_controller_pane.py) |

NyXのローカルHEADは648f860e5afd582f80aa58645570a16bfc68a393。作業ツリーの未コミット差分は未確認であり、その接続管理は移植しない。利用者はCodexの停止後、接続済み機器へマウス操作できる。実行中の開閉は表示だけを変える。

## 2. 対象範囲

InputServiceの手動更新・手動限定解除、開始要求内の解除待ち、アプリ配送時の受付確認、GUIモデルと小窓、終了要求との結合、実画面・実機受入。

## 3. 対象外

キーボード割当・複数入力源合成・タッチ・押下固定・連打・Bluetooth・入力専用スレッド。受付世代・調整役・汎用イベント基盤は作らない。現行MCPの有限列・観測・記録契約を変更せず、手動操作だけで会話やrecordsを生成しない。

## 4. 関連文書・着手条件

[02](../../initial/02-runtime.md)、[03](../../initial/03-game-io.md)、[09](../../initial/09-control-flows.md)、[10](../../initial/10-ui-interaction.md)、[入力API](../../../docs/input-api.md)。段階1はc991671、段階2はc247633・362ad44・9713b8d。公開型・docstringをレビューし、Product Designの採用画像とnative Qt画面を比較する。

## 5. 振る舞い仕様

### 手動入力と開始

手動更新は完全なInputStateを渡す。入力側が一つの操作taskと手動状態列を所有し、短いボタンの押下→解放も順序どおり送る。重複する同じ状態は送らない。未送信の末尾と同じボタン状態なら軸更新は最新値へまとめ、遅いwrite中に過去のドラッグを蓄積しない。ボタン状態の遷移は残す。GUIの軸ドラッグ更新は16ms間隔を上限とし、押下・解放は遅延させない。

update_manual(neutral)は保持中の中立更新であり、中央を通過して再びドラッグできる。release_manualが操作の終了を表す。解除を受け付けた操作はneutral完了まで所有し、その間に次の保持を受け付けない。有限列と手動保持は同時に実行しない。機器能力は入力側でも検査する。

release_manualは手動だけを解除して捕捉taskを待つ。呼出し側が取り消されても入力側が解除を保持する。Codexの停止・切断が回収するのは捕捉した有限操作だけであり、後から受理した手動入力を止めたり待ったりしない。アプリ共通停止・終了は手動も含めて解除を待つ。終了時の解除失敗を成功にしない。

既存_Submissionに実行開始を伴うかの属性だけを加える。send・Goal再開は開始対象、Goal設定は未読込またはGoal不在の場合だけ開始対象とする。読込済みGoalの編集・削除・モデル変更は開始対象にしない。_prepareの先頭で手動解除をawaitするため、thread/resumeも解除後に送る。追加指示の手動解除は有限操作列へ作用しない。解除失敗は入力診断を通知し、既存CodexConnectionErrorで未送信を返す。過去の失敗を後続の会話へ再適用しない。

アプリはCodexの開始要求・turn・active Goal・回答待ち・停止中と終了要求を確認し、入力側は接続・切替・所有を確認する。開始と手動更新は同じループの配送task順で処理し、先行開始要求の登録前に手動受付を差し込まない。GUI無効化では押下・ドラッグ表示を空にし、受付再開でも復元しない。

### ウィンドウと操作部

表示→コントローラから一つだけ開く。初期640×420、最小520×350論理pxを出発点に実画面で調整する。ネイティブのタイトルバー、QPalette基調、左シアン・右赤を使う。肩ZL/L/LS・RS/R/ZR、左上スティック・右上ABXY、中央2×2、左下十字・右下スティック。見えるスティック名・状態バー・接続先・説明文は置かない。accessibleNameは識別のため付ける。

ボタンはマウス保持、stickは円内に制限した-100..100（右・下が正）、padは8方向と中央。キーボードのSpace等が偶然発火しないようfocus・shortcut・autoRepeatを無効にする。CH552/PokeConは全操作、3DSはSELECT/STARTに置換しLS/RS/CAPTUREを無効表示する。配置は保持する。

close/minimize/WindowDeactivateで保持していた手動だけを解除し、内部のdragging/downも解除する。再表示は無送信で位置を保持する。部品間のFocusOutを小窓の非アクティブ化と混同しない。初回は主画面と同じ画面に収める。入力失敗は既存NotificationCenterへ残す。

Shellの既存_exit_requested/committedに従って、設定保存・終了確認中から受付を閉じる。取消しや保存失敗で要求撤回した場合も旧押下を復元しない。提出済み接続設定は終了確定までは既存手順を維持する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | 手動保持の更新・短い押下・解除を順序どおり送り、有限列との排他・protocol・失敗・終了回収を守る | new / edge | integration | item 1、677a425。API未定義のred→7件green。軸集約・中央通過・取消された解除待ち・task完了直後の再受付も確認。追加構造変更不要 |
| refactor-skipped | 開始対象だけが解除後に準備・送信へ進み、失敗は未送信、取消しでは復活せず、編集・追加送信は有限列を止めない | new / edge | integration | item 2、8e32662。解除せず送信するred→11件green。追加構造変更不要 |
| green | 実行中・Goalのターン間は手動拒否し、Codex切断が新旧の手動入力へ干渉しない | new / edge | integration | item 3。アプリ配送と捕捉対象、終了要求取消しの順序。8件green。コミット後に完了記録 |
| todo | マウスのボタン・stick・padが状態と押下表示へ変換され、解放・無効化・窓イベントで復元しない | new | unit / integration | item 4。モデルと小窓、protocol別 |
| todo | 小窓を一つだけ開き、実行中の開閉・終了取消し・保存失敗を経ても入力・接続を誤操作しない | new / edge | integration | item 5。Shell結合と既存終了経路 |

## 7. 設計メモ

入力の実解除はInputServiceに限定し、GUIは状態更新・手動解除依頼だけを送る。CodexServiceへウィンドウの存在を知らせない。アプリは既存通知のたびに実所有から手動受付可否を計算し、GUIへ派生したboolを渡す。GUIに判定を複製せず、別の状態機械を作らない。マウス用モデルは機器やCodexへ依存せず、後日のキー操作も同じInputStateへ変換できる境界にする。

## 8. 対象ファイル

services/input.py・codex.py・application.py・_conversation_execution.py、app/shell.py、ui/main_window.py、新しいcontroller_model.py・virtual_controller.py、関連unit/integration test、初期設計・利用docs。

## 9. 検証

各項目のred/greenと既存回帰、全品質gate、採用モックとnative画面の比較を実施する。実画面は標準・最小寸法、DPI相当の拡大、フォント拡大、無効・3DS表示、ウィンドウ外でのマウス解放を確認する。COM8/CH552とUSB Video Deviceを候補にし、実際に列挙・映像を確認してから短いゲーム操作を行う。機器ACK・正確な解除時間、他protocol実機・長時間運転を確認済みにしない。

| command / 方法 | result | notes |
|---|---|---|
| 段階3のTDD・品質gate | not run | 着手時点 |
| uv run pytest tests/integration/test_manual_input.py tests/integration/test_input_service.py tests/integration/test_input_configuration.py -q | pass | 入力単独の初期5件と回帰、28 passed（1.99秒） |
| uv run pytest tests/integration/test_manual_input.py tests/integration/test_codex_shutdown.py tests/integration/test_input_service.py -q | pass | 軸集約・中央通過を追加後28 passed（1.97秒）。manual単独は6件 |
| 独立レビュー・再現テスト | 修正 | 手動task完了から所有解除callbackまでの隙間に新しい入力を受理して失うP2を採用。再現red後、旧manualが残る間の新規受付を拒否する最小修正 |
| uv run pytest tests/integration/test_manual_input.py tests/integration/test_input_service.py tests/integration/test_input_configuration.py tests/integration/test_codex_shutdown.py -q | pass | 修正後35 passed（2.82秒）。manual単独7件。ruff check/format・tyもpass |
| uv run pytest tests/integration/test_manual_execution.py tests/integration/test_conversation_control.py tests/integration/test_conversation_resume.py tests/integration/test_conversation_execution.py -q | pass | item 2、63 passed（13.63秒）。Goal通知を返す既存ResumePeerで実際の完了条件を検証 |
| 独立レビュー→ケース補強 | 採用 | 読込済みGoal不在での新規Goal作成、send/goal_set両方の未送信結果を追加。修正必須の実装指摘なし |
| uv run pytest tests/integration/test_manual_execution.py -q | pass | 補強後11 passed（2.89秒）。ruff check/format・tyもpass |
| 独立レビュー・再現テスト | 修正 | 終了要求と取消しの同期callbackが先行する手動更新taskを追い越すP2を採用。要求前後2列でredを確認し、終了要求も既存task配送順へ揃えた。受付世代や別の調整役は追加していない |
| uv run pytest tests/integration/test_manual_application.py tests/integration/test_manual_input.py tests/integration/test_input_configuration.py -q | pass | 順序修正後21 passed（2.26秒） |
| uv run pytest tests/integration/test_manual_application.py tests/integration/test_codex_service.py tests/integration/test_codex_shutdown.py -q | pass | 58 passed（11.16秒）。item 3単独8件。ruff check/format・ty・diff checkもpass |
| native画面・画像比較・実機 | not run | 実装後に記録 |

## 10. 先送り事項

キーボード、タッチ、長時間運転は親仕様第10節へ。機器や実行状態から独立した汎用管理は導入しない。

## 11. チェックリスト

- [x] 配置・責務・既存機構との境界を確認
- [ ] TDD項目を検証・コミット
- [ ] 初期設計・公開API・利用手順を更新
- [ ] 品質gate・採用画像比較・実機の結果と限界を記録
- [ ] 親の全受入観点へ結果を対応付ける
