# ドキュメント

## 目的と全体像

| 文書 | 内容 |
|---|---|
| [要求](requirements.md) | 製品で実現したいことと対象範囲 |
| [開発計画](roadmap.md) | 現在の到達点、次の到達点と優先順 |
| [アーキテクチャ](architecture.md) | 責務・依存・プロセスとスレッドの構成 |

## 現在の仕様

| 領域 | 文書 |
|---|---|
| 機能 | [会話・Goal・再開](design/functions/conversations.md)、[プロジェクト管理](design/functions/projects.md)、[ゲームI/O](design/functions/game-io.md) |
| 画面と操作 | [デスクトップGUI](design/ui/desktop.md) |
| Codexとの接続 | [通信と実行設定](design/interfaces/codex.md)、[接続・管理API](design/interfaces/codex-api.md)、[アプリケーションサービスAPI](design/interfaces/application-api.md) |
| 映像・入力との接続 | [映像取得API](design/interfaces/video-api.md)、[入力API](design/interfaces/input-api.md)、[ゲームI/O MCP](design/interfaces/observation-mcp.md) |
| データと保存 | [データの配置・所有](design/data/storage.md)、[観測・記録APIと保存形式](design/data/recorded-observation.md) |

## 利用・開発の手順

- [起動](../README.md)、[設定](guides/settings.md)、[映像プレビュー](guides/video.md)、[Codexとゲーム操作](guides/codex.md)
- [開発環境と検査](guides/development.md)、[実接続の確認手順](guides/verification.md)、[配布と公開](guides/publishing.md)

過去の設計・計画・検証記録は[アーカイブ](../spec/archive/README.md)、完了した作業の詳細は[完了資料](../spec/complete/)にあります。開発エージェントの運用は[AGENTS.md](../AGENTS.md)を参照してください。
