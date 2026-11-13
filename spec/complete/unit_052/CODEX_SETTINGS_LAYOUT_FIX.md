# Codex設定の配置と認証導線の修正

## 目的・起点
2026-11-13、unit051の実画面レビューで固定高さと行内位置の不整合を確認。起点3f216fb、開始時clean。既知の表示不具合の修正だけを独立して行う。

## 対象・対象外
上段の一つの括りに「Codex App Server」「サンドボックス環境」「認証」を並べ、その下に利用状況、接続時の既定値を置く。「認証」は旧ChatGPTアカウントの行ラベルであり、独立したセクションにはしない。短い値は選択可能なラベル、利用状況は内容に応じた高さと上限を持つ既存Transcript。通常ボタンと行の位置を揃える。
通信API・認証方式・自動ログイン・自動再接続・U07以降・PR/マージは対象外。

## 関連・前提
[前回実装](../../complete/unit_051/CODEX_SETTINGS_UI.md)、[UI](../../initial/10-ui-interaction.md)、[接続手順](../../../docs/codex-gui.md)。Product Designレビューの指摘を起点とし、機能テストの成功を見た目の合格としない。

## 振る舞い
未接続時は認証状態を断定せず—とし「ログイン」を無効表示。「Codex App Server」の接続ボタンで既存connectを明示実行しaccount/readを待つ。認証ボタンから接続する導線は設けない。未認証ならログイン、認証済みなら取得値。ブラウザは本人の明示操作で開く。App Serverの切断とChatGPTのログアウトを混同しない。設定開閉で接続・認証を自動実行しない。
未取得の一行状態に大きな固定高さを予約しない。利用状況は折返しと上限付きスクロール、値の選択/コピー・更新中の閲覧保持を維持する。

## TDD Test List
|状態|項目|層|
|---|---|---|
|refactor-skipped|未接続のログイン無効化と接続後の既存ログイン操作、可変表示の収まりと閲覧保持|既存unit/integration改訂|

## 設計・対象ファイル
codex_settings、connection_panel、既存Qt試験と利用手順。QActionの既存可否を再利用し、別の接続状態機械は作らない。

## 検証
未取得時の過剰高さをredで再現。認証から接続する初案は最終合意で取り下げ、既存GUI受入を改訂して未接続のログイン無効化のredを確認。接続→ログイン、連打、再表示、ラベルの選択/コピーを確認した。利用状況の上限付き表示・内容更新・選択/スクロール保持・空表示への縮小も確認。

- `uv run pytest tests/unit/test_codex_settings.py tests/integration/test_codex_gui.py -q --basetemp=tmp/u052-green`、最終`tmp/u052-focused`: 51 passed。
- 最終の括り・未接続ログイン無効化後: `uv run pytest tests/unit/test_codex_settings.py tests/integration/test_codex_gui.py -q --basetemp=tmp/u052-disabled-green`: 51 passed。ruff・ty・文書リンク検査も再実行し成功。
- `uv run pytest tests/unit tests/integration -q --basetemp=tmp/u052-all --junitxml=tmp/u052/all.xml`: 846 passed、61.05秒（最終の括り・未接続ログイン無効化前）。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`、`uv lock --check`、`uv build`: 成功。
- `uv run python scripts/check_package.py`: 通常一時領域で隔離wheel install、capture worker回収、module/script双方の起動終了成功。
- `uv run python tmp/u06-m1/validate_docs.py`、`git diff --check`: 成功。

Windows Qt描画を760×650と620×440 logicalで再作成し、接続済みと未接続を目視確認。`tmp/u052/normal.png`・`small.png`・`offline-normal.png`・`offline-small.png`。短い値の行内整列、通常ボタン、上段3項目と利用状況の分離、空表示の縮小を確認した。小画面で内容が増えた場合は外側スクロールを維持する。前回の古い画像を新しい確認の根拠にしていない。

実ログイン・ブラウザ認証・機器操作は変更しないため再実行していない。接続・account/read・account/login/startの関係は採用コードを読み、固定イベントによるUI検証と区別した。自動接続・認証キュー・新しい接続状態は追加していない。

## 先送り
認証方式追加、実機・長時間は対象外。

## 完了確認
- [x] 修正と回帰検証
- [x] 実画面と仕様の照合
- [x] コミット
