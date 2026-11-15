# プロジェクト空表示と接続既定値の設定整理

## 1. 目的・起点

2026-11-15の追加依頼。プロジェクト作成直後に空のスレッド一覧であることを示し、設定画面の接続既定値の編集欄を撤去する。

## 2. 対象範囲

サイドバー見出し、空表示、プロジェクト選択直後の展開、設定画面からモデル・推論レベルの編集欄を削除する。

## 3. 対象外

接続既定値の保存形式変更、入力欄で選んだモデルの自動保存、会話実行の変更。

## 4. 関連文書・前提

[UI仕様](../../archive/initial/10-ui-interaction.md)、[設定手順](../../../docs/settings.md)、[Codex操作](../../../docs/codex-gui.md)。unit_076の未コミット変更を保持して同じ作業ブランチ上で継続する。既存のSettingsスナップショットでGUIに出さない項目も保持できる。

## 5. 振る舞い仕様

- サイドバーの見出しとアクセシビリティ名を「プロジェクト」にする。
- 選択したProjectを展開し、通常の一覧読込を行う。取得成功後、スレッド0件かつ続きなしの場合に「スレッドはありません」を表示する。読込中・初回取得失敗・未取得では空と断定しない。
- プレースホルダーはスレッド行として扱わず、クリック・選択・実行の対象にしない。スレッドを取得したら消す。
- 設定ダイアログの「接続時の既定値」とモデル・推論レベル入力欄を撤去する。
- settings.tomlのagent.model / agent.effortは保持する。GUIで別項目を保存しても値を変更しない。直接編集はアプリ終了後に行い、次回起動・接続で読み込む。会話入力欄でのモデル選択は継続し、設定ファイルへの自動保存は追加しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | 未取得・読込中と取得済み空一覧を区別し、行追加で空表示を消す | new | integration | Qtの可視状態と行数 |
| green | 設定画面の別項目を保存してもファイル由来のモデル・推論レベルを保持 | regression | integration | 既存round-trip試験を更新 |

## 7. 対象ファイル

src/gigas/ui/conversation_sidebar.py、src/gigas/ui/settings_dialog.py、src/gigas/app/conversations.py、対応する結合試験、docs/settings.md、docs/codex-gui.md、spec/initial/10-ui-interaction.md。

## 8. 検証

- `uv run pytest tests/integration tests/unit -q`: 1026 passed。空一覧・作成直後の展開、非表示設定値の保持、保存・取消し・終了を確認。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check --no-progress`: pass。
- `uv lock --check`、`uv build`、`git diff --check`: pass。
- Qt実画面の[空表示](assets/sidebar-empty.png)と[設定画面](assets/settings-without-defaults.png)を確認。サンプルデータでの画面確認であり、実機・実サーバーでの接続検証はnot run。
- 変更した公開文書・初期仕様・本仕様の内容とローカルリンクを確認。
- 初回の追加試験ではQtのレイアウト反映待ちが不足して失敗。全体試験では削除欄から移したテスト編集が表示FPSを二重設定して失敗。それぞれ待機追加・重複編集削除後に上記の全件成功を確認。
- PR前レビュー: unit_076を含む差分の保存競合・終了・接続復元・フォルダー登録・設定値保持を確認し、未解決の指摘なし。無関係なテスト文字列差分を除去し、`uv run pytest tests/integration/test_settings_io.py -q`は14 passed。
- `uv run python scripts/check_package.py`: pass。隔離インストールからmodule/script起動・終了とcapture workerを確認。sandboxのuvキャッシュへのアクセス拒否後、通常環境で再実行して成功。

## 9. 先送り事項

none。実機接続は変更しておらず今回の検証対象外。

## 10. 完了チェックリスト

- [x] 対象範囲と対象外を確認
- [x] 全体試験と画面確認を完了
- [x] 公開文書と初期仕様を更新
- [x] 最終検証結果を記録
