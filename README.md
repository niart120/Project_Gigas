# Project GIGAS (`gigas`)

**GIGAS — General-purpose Interactive Gaming Agent System** は、汎用AIエージェント（Codex）に実機ゲームの継続攻略と操作補助を委譲するためのアプリです。

画面キャプチャーによる観測とコントローラー入力を組み合わせ、自然文の依頼からの自律プレイ、途中介入、中断・再開を行います。
ゲーム固有の攻略ロジックをアプリ本体へ組み込まず、画面理解・計画・進捗判断・記憶更新をエージェント側へ委ねる構成を採ります。

現在はWindowsのライブ映像、Codexへの画像観測・ゲーム入力、通常の依頼とGoalによる継続、一時停止と同じ会話での再開に対応しています。Messageから依頼や途中指示を送り、質問・承認には会話内のカードで応答します。提供された公開計画・推論要約・ツール活動を確認でき、会話・操作・観測と攻略メモを保存します。接続・認証・実行準備と操作手順は[Codex接続画面](docs/codex-gui.md)を参照してください。

## 計画している機能

- **計画的終了**: ゲーム内の区切りや現状メモの更新を行ってから止める経路を、現在の中断操作と分けて提供します。
- **再起動後の復帰と記憶整理**: 保存した会話・記録・攻略メモから再開する経路と、攻略メモの整理・退避・復元を追加します。

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

`uv run python -m gigas`でも起動できます。左上の歯車アイコンまたは`Ctrl+,`で設定画面を開きます。保存先、項目の意味、エラー時の対処は[グローバル設定](docs/settings.md)を参照してください。

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
- [spec/initial/README.md](spec/initial/README.md): 設計文書一覧
- [AGENTS.md](AGENTS.md): 開発エージェントガイド

## ライセンス

[MIT License](LICENSE)
