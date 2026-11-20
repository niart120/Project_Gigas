# Project GIGAS (`gigas`)

**GIGAS — General-purpose Interactive Gaming Agent System** は、汎用AIエージェント（Codex）に実機ゲームの継続攻略と操作補助を委譲するためのアプリです。

画面キャプチャーによる観測とコントローラー入力を組み合わせ、自然文の依頼からの自律プレイ、途中介入、中断・再開を行います。
ゲーム固有の攻略ロジックをアプリ本体へ組み込まず、画面理解・計画・進捗判断・記憶更新をエージェント側へ委ねる構成を採ります。

現在はWindowsのライブ映像、Codexへの画像観測・ゲーム入力、通常の依頼とGoalによる継続、共通の中断と目的を保持した再開、仮想コントローラによる手動操作に対応しています。Messageから依頼や途中指示を送り、質問・承認には会話内のカードで応答します。

会話と攻略資料を引き継いで利用でき、アプリ再起動後も同じ会話を選んで再開できます。接続・認証・操作手順は[Codex利用ガイド](docs/guides/codex.md)を参照してください。

数時間ほぼ放置で攻略を任せることが当面の目標です。現在の到達点と次の改善は[開発計画](docs/roadmap.md)にまとめています。

## 動作要件

- Windows
- Python `>=3.12,<3.16`（推奨3.13、検証済み3.12・3.13）
- [`uv`](https://docs.astral.sh/uv/)
- 設定画面の起動に機器は不要。映像プレビューにはカメラ取得に対応するキャプチャーデバイスまたは [New Nintendo 3DS XL の対応キャプチャ基板](docs/guides/video.md#new-nintendo-3ds-xl)、ゲーム入力には[対応するシリアル入力機器](docs/design/interfaces/input-api.md)を使います。

## 起動

```powershell
uv sync
uv run gigas
```

`uv run python -m gigas`でも起動できます。左上の「設定」または`Ctrl+,`で設定画面を開きます。隣のアイコンで会話サイドバーを開閉できます。保存先、項目の意味、エラー時の対処は[グローバル設定](docs/guides/settings.md)を参照してください。

## wheelから起動

リポジトリで `uv build` を実行し、生成したwheelを起動先のPCへコピーします。次はwheelを置いたフォルダーからインストールする例です。

```powershell
uv venv .gigas-env --python 3.13
uv pip install --python .gigas-env\Scripts\python.exe .\gigas-0.1.0-py3-none-any.whl
.\.gigas-env\Scripts\gigas.exe
```

## ドキュメント

- [文書一覧](docs/README.md)：要求・開発計画・アーキテクチャ・現在の仕様
- [利用ガイド](docs/guides/codex.md)、[設定](docs/guides/settings.md)、[映像プレビュー](docs/guides/video.md)
- [開発環境と検査](docs/guides/development.md)、[配布と公開](docs/guides/publishing.md)
- [AGENTS.md](AGENTS.md)：開発エージェントガイド

## ライセンス

[MIT License](LICENSE)
