# 次ターンのモデル選択

## 目的・範囲
2026-11-13の依頼で次ターン設定をM3からM1へ前倒しする。入力欄のポップオーバーで公開catalogのモデル・推論レベルを選ぶ。設定ファイルは変えない。未接続は未接続表示で操作不可。Goal接続、認証拡張、PR・マージは対象外。

## 関連・着手条件
[前回M1](../../complete/unit_047/GOAL_AND_MESSAGE_COMPOSER.md)、[親](../../wip/unit_011/GOAL_EXECUTION.md)、[UI](../../initial/10-ui-interaction.md)。開始a159468、作業branch clean。

## 契約・設計
モデル選択は接続が所有し、catalogで検証して受理後にUIへ通知する。通常turn/startは選択値を渡す。実行中のturnとsteerは変更しない。開始提出中は選択不可。MCP再構成も選択値を使用する。Goal自律turnのthread/settings/update接続はM2/M3で行う。
入力枠の表示は次ターンの選択値。ポップオーバーはモデル・推論レベルと確定ボタンのみ。利用者の指示に従い「次のターン」見出しは置かない。閉じる/Escは未確定値を破棄。対応推論レベルのみ提示しモデル変更ではそのモデルの既定値を選ぶ。未設定は既定、未接続は未接続と区別する。

## TDD Test List
- refactor-skipped D01 次ターン選択・現turn不変・不正値拒否と入力欄の選択経路（unit/integration）

## 対象ファイル
runtime/codex、services/codex、ui、app/shell、既存テスト、初期仕様・親・docs。

## 検証
実装前の追加試験はselect_model未実装によるAttributeErrorでred。実装後、通常turnの次の送信だけが変更され、steer/現turnの送信済み値が不変、不正推論レベルを拒否することを確認。GUIは未接続、対応候補、Esc、再表示、受理前後、原稿、中断、切断を確認。サービス結合は実行中に選択してもturn/interruptを発行しないことを確認。

- `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u048-release`: 746 passed。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`: 成功。
- `uv lock --check`、`uv build`、`uv run python scripts/check_package.py`: 成功。依存追加なし。隔離install・資材・capture worker終了・両entrypoint起動/終了を確認。
- Product Design観点で実Qt部品を描画し、[最終ポップオーバー](assets/model-popup.png)を確認。ラベルと選択欄の対応、余白、切れなしを確認。「次のターン」の見出しは利用者の途中指示により撤去。追加した画像は固定catalogによるQt描画で、実モデル接続の画像ではない。
- type-boundary-review/docstring-style: catalogを不変dataclassへ変換し、サービスProtocolとfakeを一致させた。型無視・cast・依存追加なし。
- KISSレビュー: 通常turn/startの既存引数と接続の選択値を再利用。新しい継続ループ、設定保存、現在turnの再開/中断、別の配送層を追加しない。UIは確定通知を受けるまで旧選択を保持し、失敗は既存通知へ渡す。

## 先送り
Goal自律継続のthread/settings/update接続、認証と候補妥当性の分離、新規/APIキー認証、実モデル/実機の再受入はM2/M3。今回は固定RPC/Qt/サービス結合で検証し、実モデルによる二つの通常turn・実IME・複数モニターの再確認はnot run。現turnの後続推論は既存の公開turn契約に従い、製品から変更RPCを送らない。MCP再公開時も既存_configure_threadが同じ選択値を使用する。

## 完了条件
- [x] 選択・原稿・停止の回帰と表示を検証
- [x] 仕様整合・品質gate・コミット

状態: 実装・検証完了。U06親はM2/M3未着手のwipを維持する。
