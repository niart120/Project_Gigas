# Project GIGAS (`gigas`)

**GIGAS — General-purpose Interactive Gaming Agent System** は、汎用AIエージェント（Codex）に実機ゲームの継続攻略と操作補助を委譲するためのアプリです。

画面キャプチャーによる観測とコントローラー入力を組み合わせ、自然文の依頼からの自律プレイ、途中介入、中断・再開を行います。
ゲーム固有の攻略ロジックをアプリ本体へ組み込まず、画面理解・計画・進捗判断・記憶更新をエージェント側へ委ねる構成を採ります。

現在はWindowsのライブ映像、Codexへの画像観測・ゲーム入力、通常の依頼とGoalによる継続、共通の中断と目的を保持した再開に対応しています。Messageから依頼や途中指示を送り、質問・承認には会話内のカードで応答します。

Project・会話・標準自動記憶の管理は、専用homeのCodex App Serverへ委ねます。サイドバーからProject所属・未所属の会話を選んで履歴を閲覧し、指示の送信やGoalの作成・再開で実行を始められます。アプリ再起動後も、同じ会話を選んで再開できます。GIGASは作業フォルダー内にゲームI/O記録・画像と提出本文・確定本文を保存し、攻略資料はCodexが同じ作業フォルダーで編集します。接続・認証・操作手順は[Codex接続画面](docs/codex-gui.md)を参照してください。

実GUI・実モデルで会話の新規開始・履歴閲覧・再開を確認しています。実機を含む確認範囲と未検証事項は[実接続の確認](docs/codex-verification.md)を参照してください。

計画的終了の専用機能と、専用の記憶整理・自動退避・復元は初期版に含めません。攻略中のメモ更新と、停止中の利用者による編集は引き続き利用できます。

## 動作要件

- Windows
- Python `>=3.12,<3.16`（推奨3.13、検証済み3.12・3.13）
- [`uv`](https://docs.astral.sh/uv/)
- 設定画面の起動に機器は不要。映像プレビューにはDirectShow対応キャプチャーデバイス、ゲーム入力には[対応するシリアル入力機器](docs/input-api.md)を使います。

## 起動

```powershell
uv sync
uv run gigas
```

`uv run python -m gigas`でも起動できます。左上の「設定」または`Ctrl+,`で設定画面を開きます。隣のアイコンで会話サイドバーを開閉できます。保存先、項目の意味、エラー時の対処は[グローバル設定](docs/settings.md)を参照してください。

## wheelから起動

リポジトリで `uv build` を実行し、生成したwheelを起動先のPCへコピーします。次はwheelを置いたフォルダーからインストールする例です。

```powershell
uv venv .gigas-env --python 3.13
uv pip install --python .gigas-env\Scripts\python.exe .\gigas-0.1.0-py3-none-any.whl
.\.gigas-env\Scripts\gigas.exe
```

## 開発コマンド

```powershell
uv sync --dev
uv run ruff format --check .
uv run ruff check .
uv run ty check --no-progress
uv run pytest tests/unit tests/integration
uv run python scripts/check_package.py
```

## ドキュメント

- [グローバル設定](docs/settings.md)
- [映像プレビュー](docs/video.md)
- [Codex接続画面](docs/codex-gui.md)
- [製品の要求](docs/requirements.md)
- [初期設計のアーカイブ](spec/archive/initial/README.md): 過去の設計・開発計画
- [AGENTS.md](AGENTS.md): 開発エージェントガイド

## ライセンス

[MIT License](LICENSE)
