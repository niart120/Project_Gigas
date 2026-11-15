# 入力設定・接続の独立所有 仕様書

## 1. 概要

状態はwip。[unit_078](../unit_078/VIRTUAL_CONTROLLER.md)段階2の子仕様。段階1（c991671）の抽出を前提に、入力設定の正本・機器生成・適用・接続終了をInputServiceへ移す。

| source | 内容 | path |
|---|---|---|
| 設計合意・実装依頼 | 並列所有と短いアプリ側連携 | [親仕様](../unit_078/VIRTUAL_CONTROLLER.md) |
| 抽出完了 | 既存回帰1027件と実接続・Windows起動終了を確認 | [unit_079](../../complete/unit_079/CODEX_SERVICE_EXTRACTION.md) |

利用者がCodexなしで入力設定を適用でき、エージェント実行中の接続変更は停止完了後に新機器へ進む。

## 2. 対象範囲

入力設定・factory・適用task・ポート列挙・InputConnectionResultを入力側へ移す。アプリ側は最新設定の受付を入力側へ渡し、必要なCodex停止を待ち、適用を依頼する。会話不在時の全入力停止はアプリ側へ移す。

## 3. 対象外

手動入力、開始要求の分類、GUI追加は段階3。入力側へturn/Goalの知識や停止コールバックを注入しない。汎用管理役・受付世代を増やさない。

## 4. 関連文書・調査

[責務](../../initial/02-runtime.md)、[ゲームI/O](../../initial/03-game-io.md)、[処理順](../../initial/09-control-flows.md)。既存test_codex_serviceの設定独立・最新変更先・再試行、test_input_serviceのclose失敗・open中終了を再利用する。独立調査で確認した停止/解除待ちの抜けを以下の項目で検証する。

## 5. 振る舞い仕様

| 場面 | 契約 |
|---|---|
| 設定受付 | InputService.request_configが最新値を保持。適用待ち中は入力を受け付けない。同一設定の正常接続は再接続・Codex停止不要 |
| 接続変更 | アプリが必要なCodex停止をawaitし、InputService.apply_configを呼ぶ。停止失敗は入力設定の結果へ返し、新機器を開かない |
| 連続変更 | 停止待ち中は最新値だけ適用。開始済みopenは回収後、最新値へ進む。明示停止は設定適用を撤回しない |
| 解除失敗 | 対象taskを捕捉して結果を回収。失敗なら新機器を開かない。次の明示設定適用では再試行できる |
| 終了確定 | 未開始の設定を撤回し、開始済みI/Oと旧所有者を回収。停止待ちが戻っても新接続を始めない |

接続変更中に通常会話の送信まで一括禁止する契約は追加しない。入力は切替中として拒否し、接続完了時の自動再開はしない。段階3の手動受付は独立してこの接続状態を参照する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | 入力サービス単独で設定適用・同一設定・切断・再接続ができる | new | integration | item 1。factory引数未定義のred→green。設定所有を移し既存Qt配送を回帰。コミット後に完了記録 |
| todo | 接続変更はCodex停止完了後に最新設定を適用し、同一設定・停止失敗・終了を区別する | new / edge | integration | item 2。停止待ちをイベント同期 |
| todo | 旧入力の解除失敗では新接続を開かず、後続の明示適用で回復できる | edge | integration | item 3。捕捉taskの完了callback後も結果を失わない |

## 7. 設計メモ

InputServiceはrequest_configとapply_configを分け、変更先を一箇所で所有する。アプリに残すtaskは停止待ちの連携だけ。入力の適用taskと機器接続taskは開始済みI/Oの取消し・回収責任が異なるため維持する。Codexの待機可能な停止窓口は会話を停止するだけとし、設定の知識を持たない。

## 8. 対象ファイル

services/input.py、application.py、codex.py、codex_models.py、関連integration test、初期設計・公開API説明。

## 9. 検証

対象テストのred/green、既存回帰、ruff・ty・lock・build、実接続とGUIを確認する。実機入力と手動コントローラの通し受入は段階3で行う。

| command / 方法 | result | notes |
|---|---|---|
| uv run pytest tests/integration/test_input_configuration.py tests/integration/test_input_service.py tests/integration/test_codex_service.py -q | pass | item 1、63 passed（9.51秒） |
| uv run ruff check src/gigas/services tests/integration/test_input_configuration.py / uv run ruff format --check src/gigas/services tests/integration/test_input_configuration.py / uv run ty check --no-progress | pass | item 1の型・書式・静的規約 |
| 実接続・Windows画面 | not run | 段階2変更後に確認 |

## 10. 先送り事項

手動更新・Codex切断の有限入力限定・開始前解除は親の段階3。

## 11. チェックリスト

- [x] 責務と調査結果、対象外を確認
- [ ] TDD各項目を検証・コミット
- [ ] 回帰と必要な品質gateを確認
- [ ] 初期設計・公開API・実接続の記録を更新
