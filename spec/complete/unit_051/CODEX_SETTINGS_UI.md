# Codex設定UIの整理

## 目的・起点
2026-11-13の採用画像と実装依頼。U06完了後のUI整理。起点2cf3367、作業ブランチcodex/u06-m0-goal-design、開始時clean。

## 対象・対象外
ログイン、サンドボックス準備、利用状況を設定のCodexタブへ集約する。接続メニューは接続・切断だけ。通信・操作権・保存処理は再利用する。APIキー、ログアウト、別認証、U07以降、PR・マージは対象外。

## 関連文書・着手条件
[U06 M3](../../complete/unit_050/GOAL_ACCEPTANCE.md)、[UI](../../initial/10-ui-interaction.md)、[設定](../../../docs/settings.md)。M3受入済み。

## 振る舞い
ChatGPTアカウント、Codex App Server、Codexサンドボックス、利用状況、接続時の既定値の順。開くだけでは接続しない。設定内で明示接続・切断、ログイン開始・URLを開く、準備を操作する。状態と操作可否は既存接続パネルが所有。設定画面を閉じても提出済み操作は継続し、再表示で最新状態を表示する。値は選択・コピーでき、利用状況更新で閲覧位置を維持する。
利用枠は取得した期間・使用済み割合・リセット日時を表示。未取得・不明を0で補わない。準備成功後に非readyなら再接続必要として扱い、準備失敗と区別する。自動再接続は行わない。

## TDD Test List
|状態|item|層|
|---|---|---|
|refactor-skipped|設定内の操作・状態更新・閲覧保持・閉じ再表示、接続メニュー整理、準備後の再接続状態|既存unit/integration改訂|

## 設計・対象ファイル
既存account表示を設定内の部品へ変更し、QActionを設定ボタンと共有する。設定保存値と接続操作を分離する。ui、app/shell、sandbox通知境界、既存tests、docsと初期UI仕様。

## 検証
既存メニュー受入2件と準備成功後非readyの既存試験1件でredを確認した。関連213件成功後、全unit/integration 845 passed（78.61秒、tmp/u051/all.xml）。描画修正後のUI関連50件、再表示・キーボード操作を改訂したGUI49件も成功。

実行command:
- `uv run pytest tests/unit tests/integration -q --basetemp=tmp/u051-all --junitxml=tmp/u051/all.xml`
- `uv run pytest tests/integration/test_codex_gui.py tests/unit/test_codex_settings.py -q --basetemp=tmp/u051-ui-final`
- `uv run pytest tests/integration/test_codex_gui.py -q --basetemp=tmp/u051-reopen`
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`、`uv lock --check`、`uv build`成功。
- `uv run python scripts/check_package.py`は検証環境の一時領域権限で失敗。通常権限で再実行して隔離インストール・capture終了・module/script起動終了に成功。
- `uv run python tmp/u06-m1/validate_docs.py`でリンク・unit一意性・仮文言を検査。`git diff --check`成功。

`tmp/u051/render.py`で実Qt部品に固定通知を渡し、760×650と620×440 logicalのWindows描画を確認（normal.png/small.png）。余分な背景、接続ボタンの不要なメニューを修正。小画面は縦スクロールし、下部操作を固定する。実ログイン・OS準備・実機入力は再実行していない。公開RPC変更はなく、準備後非readyの通知分類だけを固定peerで検証した。新しい自動接続・再試行・所有層は追加しない。

型/docstring/利用手順を照合。SandboxEventの既存readinessを保持し、準備成功後のreconnect_requiredだけを追加した。閉接続へ遅い成功を出さず、実際の失敗・取得不能は従来例外を維持する。既存の入力権限、利用不能停止、非同期保存は変更しない。

## 先送り
新しい認証方式、利用枠枯渇の実発生、長時間運転は対象外。

## 完了チェック
- [x] 実装・既存試験改訂
- [x] 品質gateと実画面確認
- [x] 文書整合・コミット
