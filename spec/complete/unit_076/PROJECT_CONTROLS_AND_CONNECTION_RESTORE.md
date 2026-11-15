# プロジェクト操作と接続復元

## 1. 目的・起点

2026-11-15のユーザー依頼を起点とする独立した修正仕様。作業中の追加指摘により、[U08の採用モック](../../complete/unit_068/assets/conversation-sidebar-proposal.png)とボタンの見た目も照合した。上部のプロジェクトメニューを撤去し、サイドバー下部からフォルダーを選び、再起動時には前回の接続先へ接続する。

## 2. 対象範囲

プロジェクト作成導線、フォルダー選択、映像・入力・Codexの起動時接続。

## 3. 対象外

会話・Goalの自動再開、ログインの自動開始、接続失敗後の自動再試行、機器探索による接続先の置換。

## 4. 関連文書・着手条件

[UI](../../initial/10-ui-interaction.md)、[実行](../../initial/02-runtime.md)、[機器](../../initial/03-game-io.md)、[保存](../../initial/04-agent-memory.md)、[設定](../../../docs/settings.md)。既存の設定保存・接続・Project登録を利用する。mainは作業開始時clean、専用ブランチで作業する。

## 5. 振る舞い仕様・設計

- サイドバーのスクロール領域の下に「＋ プロジェクト」ボタンを固定配置する。左右に余白を設けて幅を広げ、36論理px以上の高さを確保する。既存の切替可能条件を維持する。
- フォルダーピッカーの選択先を既存のopen_project経路へ渡す。同じrootの登録済みProjectは再利用し、未登録なら登録する。資料を変更せず、取消しでは何もしない。
- 読込成功後、保存済み映像・入力設定を一度適用する。未接続設定なら接続しない。設定読込失敗・終了要求後は開始しない。
- Codexの接続成功を保存し、次回起動で一度接続する。明示切断の成功で記憶を解除し、通常終了・通信障害では保持する。設定保存中の結果は現在の保存完了後に反映し、別の設定を巻き戻さない。
- 接続失敗は既存通知へ残す。自動接続だけで会話実行・Goal再開はしない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | フォルダー選択による登録・再利用と取消し | regression | integration | 既存Project GUI試験を更新 |
| green | 保存先への起動時接続と読込失敗時の抑止 | new | integration | 模擬接続境界 |
| green | Codex成功の保存・再起動・明示切断・保存競合 | new | integration | 模擬app-server |

## 7. 対象ファイル

src/gigas/app/shell.py、src/gigas/ui/main_window.py、src/gigas/storage/settings.py、対応するtests/integration、docs、spec/initial、plus.svgとライセンス、[画面照合記録](../../../design-qa.md)。

## 8. 検証

- red: フォルダー選択試験は旧メニューの有効条件との不一致、起動時接続試験は接続要求0件、Codex復元試験は保存項目なしで失敗することを確認した。
- green: Project GUI、自動接続、設定適用、終了、設定画面の対応試験が成功した。`uv run pytest tests/integration -q`: 597 passed。起動・保存・終了・Project操作・UI・機器境界の回帰を確認。
- `uv run pytest tests/unit --basetemp=tmp/pytest-unit-restoration -q`: 428 passed。独立した一時ディレクトリで単体試験を実行。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`: pass。書式・lint・型境界を確認。新しいignore/castはない。
- `uv lock --check`、`uv build`: pass。公開Settingsに真偽値を末尾追加し、既存の位置引数の順序を保持。docstringと設定文書を更新した。wheel内のplus.svgとLUCIDE_LICENSEの同梱をZipFileで確認。
- 変更文書のローカル参照先を検査し、本文を読んで旧起動動作との矛盾と仮テキスト残りを確認。`git diff --check`: pass。
- Windows実画面: 上部メニュー撤去、下部ボタンの幅・余白・アイコンと文字の位置を確認。詳細は画面照合記録。
- not run: 実機を使う再起動後の自動接続、ネイティブフォルダーピッカーの実操作、長時間運転。接続と選択は模擬境界で検証。

## 9. 先送り事項

実機の抜去・再接続や長時間運転は今回の自動試験で保証しない。

## 10. 完了チェックリスト

- [x] 対象範囲・対象外を確認
- [x] 実装とテストを完了
- [x] 初期仕様・公開文書を更新
- [x] 検証結果と未検証事項を記録
