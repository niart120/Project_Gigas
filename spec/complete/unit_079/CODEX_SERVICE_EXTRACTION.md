# Codexサービス抽出 仕様書

## 1. 概要

状態はcomplete。unit_078の段階1を担当する子仕様。Qtのスレッド・配送とCodex固有処理を分け、既存の入力・停止・会話管理の挙動を維持した。実装コミットはc991671。

### 起点 / source

| source | 内容 | path |
|---|---|---|
| ユーザー依頼 | unit_078をTDDで完遂する | [親仕様](../../complete/unit_078/VIRTUAL_CONTROLLER.md) |
| 現行実装 | 調査基準efa4237、仕様確定8b00d3e | [codex.py](../../../src/gigas/services/codex.py) |

### use case

| actor / boundary | 入力・状態 | 期待結果 | 制約 |
|---|---|---|---|
| GUI | 接続・送信・停止・切断 | 既存の結果通知と資源回収を維持 | GUIを待たせない |
| asyncio呼出し側 | Qtスレッドを作らずCodexへ接続・送信・切断 | 同じ会話処理を利用でき、借用した入力接続は保持 | 終了責任は各所有者にある |

## 2. 対象範囲

ApplicationServiceThreadへQt配送とループ寿命を残し、CodexServiceへ会話・接続・結果解釈・Codexタスクを抽出する。既存の会話部品を維持する。呼出し元・テスト・公開説明を新しい名前へ更新する。

## 3. 対象外

入力設定適用の入力側への移動、接続切替の順序変更、手動入力、GUI追加は後続段階。互換クラスや新しい管理レイヤは作らない。

## 4. 関連docs・前提

[責務](../../archive/initial/02-runtime.md)、[処理順序](../../archive/initial/09-control-flows.md)、[GUI API](../../../docs/codex-gui.md)。関連既存テスト102件のgreenを確認済み。構造整理には既存回帰テストを使い、人工的なredを作らない。新しい非Qt境界の利用を一つのTDD項目として先に検証する。

## 5. 振る舞い仕様

接続世代・未送信／結果不明・古い通知の破棄、Goal・回答・会話切替・記録の帰属を維持する。CodexService.closeは自身の通信・会話・記録を回収し、入力接続のcloseはアプリ側で行う。アプリ終了は両者を待ち、一方の失敗でも他方を回収する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | Qtのスレッドを作らず接続・通常送信・切断の結果を受け取り、Codex終了後も借用した入力機器を利用できる | new | integration | c991671。test_codex_coreでred→green。既存GUI境界を抽出先へ接続し1027件の回帰を確認 |

## 7. 設計メモ

CodexServiceは同期コールバックで型付きの結果を通知する。ApplicationServiceThreadがQt Signalへ配送する。Codex固有タスクとアプリの入力適用タスクの所有を分ける。GUIから未配送の要求はアプリ側が既存結果型で拒否通知する。段階1では入力設定適用をアプリ側に残す。

## 8. 対象ファイル

services/codex.py、services/application.py、services/codex_models.py、appの呼出し元、関連integration test、初期設計・GUI API説明。

## 9. 検証

| command / 方法 | result | notes |
|---|---|---|
| uv sync --dev / uv lock --check | pass | 依存変更なし |
| uv run pytest tests/integration/test_codex_service.py tests/integration/test_conversation_control.py tests/integration/test_conversation_switching.py tests/integration/test_codex_shutdown.py -q | pass | 抽出前102件、22.22秒 |
| uv run pytest tests/integration/test_codex_core.py -q | red→green | CodexService未定義のImportError→1 passed（1.23秒） |
| uv run pytest tests/unit tests/integration -q | pass | 1027 passed（92.29秒）。既存の停止・Goal・回答・会話切替・入力・GUIの回帰 |
| uv run ruff format --check . / uv run ruff check . / uv run ty check --no-progress | pass | 296ファイル、型境界・docstringを確認 |
| uv build / git diff --check | pass | sdist・wheel作成、空白エラーなし |
| uv run python -X utf8 tmp/u79_live.py | pass | 非Qt CodexServiceから実app-serverへ接続しGIGAS_U79_OK応答、通常送信accepted・完了後stop・切断・close。進行中の実接続停止は後段の通し受入で確認 |
| uv run python tmp/u79_gui.py / Computer Use | pass | 隔離settingsでWindowsの既存主画面・メニュー・会話領域を目視確認。閉じる操作後exit code 0。機器操作なし |

独立レビューではP1/P2の新規回帰なし。公開docstringの字下げとGUI/Signalの古い説明というP3指摘を採用し修正した。入力設定からCodex privateへの参照は段階2で解消する移行状態であり、完成構成として残さない。

## 10. 先送り事項

入力設定管理は親の段階2、手動操作は段階3で扱う。

## 11. チェックリスト

- [x] 対象範囲・対象外・変更前のgreenを確認
- [x] 非Qt境界のTDD項目をコミット
- [x] 既存の停止・切断・会話管理を回帰確認
- [x] 型・docstring・公開説明・必要なgateを確認
- [x] 実接続とGUIの確認結果を記録
