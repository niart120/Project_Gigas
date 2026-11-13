# 認証行への操作集約

## 目的・起点
2026-11-13のユーザー合意。091c2fdから開始、作業ツリーclean。接続時の既定値から認証方式を外し、Codexセクションの認証行へ集約する。

## 対象・対象外
設定ダイアログ・認証表示・旧api_keyの明示的な設定変更を対象とする。APIキー認証の実装、通信API変更、自動ログイン、PR・マージは対象外。

## 関連文書・着手条件
[配置修正](../../complete/unit_052/CODEX_SETTINGS_LAYOUT_FIX.md)、[初期UI](../../initial/10-ui-interaction.md)、[設定手順](../../../docs/settings.md)、[接続手順](../../../docs/codex-gui.md)。対応方式はChatGPTのみ。設定読み書きはapi_keyを保持し、接続処理は未対応として拒否する既存契約を確認した。

## 振る舞い
- 接続時の既定値はモデル・推論レベルのみ。通常の認証行はChatGPTの状態とログインボタンを表示し、方式選択メニューは作らない。
- 旧api_key設定の場合だけ、認証行のログインボタンを「APIキーからChatGPTに変更」に置き換える。これは設定の編集操作であり、接続・ログインは開始しない。
- 変更は既存の適用／OKで保存する。キャンセル、未編集の保存、保存失敗では保存済みapi_keyを勝手に置換しない。接続には引き続き最後に保存できた設定を使う。
- 未接続時の通常ログイン無効化と接続後の明示ログインは維持する。保存中は旧方式からの変更も無効化する。

## TDD Test List
|状態|項目|層|
|---|---|---|
|refactor-skipped|旧値の保持、認証行からの変更、キャンセル・保存・接続時の設定反映|既存integration改訂|

## 設計・対象ファイル
settings_dialogが編集値と旧方式変更ボタンを所有し、shellが認証行へ配置する。codex_settingsは受信したアカウント状態を表示する。既存の保存経路と設定snapshotを使い、新しい認証方式状態機械は作らない。

## 検証
既存の全設定往復・保存失敗試験を改訂し、旧設定の変更→キャンセル→保存→接続の試験を追加。変更ボタンが存在しないredを確認後、次のgateを通過した。

- `uv run pytest tests/integration/test_app_settings.py tests/integration/test_codex_gui.py tests/unit/test_codex_settings.py -q --basetemp=tmp/u053-focused-final`: 71 passed。
- `uv run pytest tests/unit tests/integration -q --basetemp=tmp/u053-all --junitxml=tmp/u053/all.xml`: 847 passed、78.32秒。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`: 成功。
- `uv run python tmp/u06-m1/validate_docs.py`、`git diff --check`: 成功。

ApplicationShellで実際に配置したQt画面を760×650／620×440で描画し、通常／旧api_keyの両状態を目視確認（tmp/u053/chatgpt-small.png、api_key-small.pngほか）。認証方式の選択欄がなく、変更ボタンが認証行に収まり、キャンセル・保存失敗時に旧設定を保持することを確認。既存の設定snapshot・保存経路を利用し、認証キューや自動接続を追加していない。

実ログイン・Windows準備・実機操作は通信処理を変えないため再実行していない。lock確認・package buildは依存・メタデータ・配布構成を変えないため今回は未実行。

## 先送り
複数認証方式のメニューは、方式を追加する工程で扱う。

## 完了確認
- [x] 実装と既存テスト改訂
- [x] 描画・仕様・検証結果の照合
- [x] コミット
