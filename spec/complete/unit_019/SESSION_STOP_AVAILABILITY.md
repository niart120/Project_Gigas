# 設定障害時のセッション停止 仕様書

## 目的・起点

状態: complete。U02完了後レビューR01を受け、設定再読込失敗でも既存セッションの中断・切断を維持する。ユーザーの即時修正着手要求に基づく独立した修正単位。

## 対象範囲・対象外

設定の有効性と接続パネルの操作可否を分離する。新規接続には有効な設定を要求する。終了中・サービス終了後の操作禁止を維持する。R02〜R05、モデル変更、認証方式の追加は対象外。

## 関連文書・着手条件

[レビュー](../../dev-journal.md)、[U05](../../planned/unit_010/INPUT_AND_STOP.md)、[実行設計](../../initial/02-runtime.md)。既存Qt画面と模擬通知で再現済み。外部プロトコルの追加確認は不要。

## 振る舞い・設計

設定読込失敗中も、既存セッションへの操作を接続状態に従って許可する。新規接続の可否だけに設定の準備状態を使う。設定障害を解消するまで新規接続は禁止。設定ファイルは変更しない。

## TDD Test List

| 状態 | 項目 | 種別・層 |
|---|---|---|
| refactor-skipped | 実行中に設定読込が失敗しても中断・切断を送信でき、切断後の新規接続は禁止される | 回帰・GUI結合 |

## 対象ファイル

`src/gigas/app/shell.py`、`src/gigas/ui/connection_panel.py`、`tests/integration/test_codex_gui.py`、利用者向けGUI手順。

## 検証

`uv run pytest tests/integration/test_codex_gui.py -k settings_load_failure -q`: 中断ボタンが無効のため期待したredを確認。修正後に単体・結合、ruff、ty、差分・文書参照を確認する。実認証・物理機器の確認は対象外。

修正後は同commandが1 passed。`uv run pytest tests/unit tests/integration -q`は146 passed。`uv run ruff check .`、`uv run ruff format --check .`（96 files）、`uv run ty check --no-progress`、`uv lock --check`、`uv build`が成功。引数はboolで型・docstringを照合し、ignore/castは追加していない。構造整理は不要と判断した。Qtの操作とsignal配送を確認しており、実ランタイムでの再確認は未実施。

## 先送り・チェックリスト

R02・R04は次の接続画面拡張前、R03・R05はU06へ引き継ぐ。

- [x] 対象範囲と失敗を確認した
- [x] 修正と回帰確認を完了した
- [x] 検証結果・後続参照を更新した
