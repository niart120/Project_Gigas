# U05 観測・保存・入力境界の整理

## 1. 目的と状態

状態: **complete（修正・構造整理・再レビュー完了）**。U05 M1レビューで確認した責務の混在と記録の不整合を修正した。起点は2026-11-12のユーザー依頼と、[M1](../unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)の実装。開始時は `2bcdbe4`、作業ツリーはclean。

## 2. 対象範囲

最新観測と事後観測の入口、共通記録窓口の生成・注入、保存形式と通信能力の検証、停止時の送出結果、後から判明するturn IDを整理する。既存試験の改訂を優先する。

## 3. 対象外

M2のGUI・MCP入力公開、実機受入、Bluetooth、追加の管理クラスやイベント基盤は扱わない。観測契約は[観測簡素化](../unit_040/LATEST_CACHE_OBSERVATION.md)に従う。

## 4. 前提と関連文書

[初期実行管理](../../initial/02-runtime.md)、[ゲームI/O](../../initial/03-game-io.md)、[記録](../../initial/04-agent-memory.md)、[U05親](../../complete/unit_010/INPUT_AND_STOP.md)、[M0](../unit_036/INPUT_AND_STOP_DESIGN.md)に従う。基準となる既存試験602件、静的検査とビルドは成功済み。

## 5. 振る舞い仕様

入力・観測・保存の失敗と停止は[責務分離](../unit_041/INPUT_FAILURE_SEPARATION.md)に従う。

観測サービスは最新画像のobserveを提供し、単独観測と操作後の画像加工・保存依頼を共用する。独立したinput_observationモジュールは撤去する。

共通RecordingServiceは組立箇所で一度生成し、観測と入力へ渡す。入力サービスの構築によるcallback上書きや観測thread文脈の変更を行わない。保存失敗は組立側から診断へ通知する。終了は符号化終了、共通保存のdrain、writer解放の順を守る。

保存済み入力列の検査は共通入力形式だけを扱い、現在対応するprotocolを推測しない。実行可能な入力の検査は選択protocolに対して受付側で行う。

送信中の停止でも開始済みwriteの結果を回収し、全byte成功はpress・neutralとも送出済みとして記録する。残りの列は実行しない。run開始後に判明した実turn IDを同じrunへ対応付け、以後の操作・終端記録へ使う。古いrunからの通知で現在の文脈や操作権を変更しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | R01 保存した入力列は現在のprotocol候補に依存せず検査・読込できる | regression | unit/integration | 期待失敗2件→関連131件成功。既存保存試験で能力変更後の再読込を確認。a974947 |
| refactor-done | R02 最新・事後観測と操作は共通保存を明示的に共有し、停止と保存の寿命を維持する | characterization | integration | 観測17件、実事後観測を通る入力24件、Codex・capture・MCP36件成功。全体604件成功 |
| refactor-done | R03 press送信中の停止でも回収済み成功を結果と記録に残す | regression | integration | 既存neutral停止試験をpressにも拡張。期待失敗1件→2件成功。49351c3 |
| refactor-done | R04 run開始後に判明したturn IDを記録し、旧runの通知を拒否する | regression | integration | 既存操作記録・旧Grant試験を改訂。期待失敗2件→関連23件成功。09bf33d |

## 7. 設計メモ

新しい抽象化を設けず、既存サービス間の依存を明示する。開始記録の非同期投入、単一所有者、操作取消し、終了時のI/O回収、結果と画像の非同期保存は維持する。

## 8. 対象ファイル

`src/gigas/services/`、`game_io/input/models.py`、`storage/input_events.py`、対応する既存試験、公開docs、関連仕様。

## 9. 検証

以下は境界整理実施時点の記録。最新キャッシュ統一後の検証は[観測簡素化](../unit_040/LATEST_CACHE_OBSERVATION.md)を参照する。

2026-11-12、Windows/Python 3.12.10で実施した。

| command | 結果・対象 |
|---|---|
| `uv run pytest tests/unit tests/integration -q` | 604 passed。入力・観測・保存と既存GUI/MCP/Codex結合の回帰 |
| `uv run ruff format --check .` / `uv run ruff check .` | pass。書式・lint・公開docstring |
| `uv run ty check --no-progress` | pass。注入する共通保存・観測API・turn IDの型境界 |
| `uv lock --check` / `uv build` | pass。依存整合・sdist/wheel生成。依存追加なし |
| `git diff --check` | pass。差分の空白検査 |

独立レビューでは、担当を入れ替えて観測・保存の終了順、形式と能力の検査、送信結果とturn ID、試験の実経路を確認した。追加の必須修正なし。受付用readerのdocstringを現責務へ修正した。CaptureServiceが同じロックで状態とFrameStoreを更新・参照するため、事後観測側のFrameとsnapshotの二重世代照合を省いても、操作受付世代との照合は維持できる。

仕様とdocsは最終契約・参照先・未確認表示を目視確認した。ローカル文書検査でリンク先・unit番号の一意性・コードフェンス・仮テキストも確認した。実接続・実機・M2の入力公開はnot runであり、模擬試験の成功を実機保証とはしない。

## 10. 未確認・先送り

機器内部遅延・実ゲームへの反映は未検証。長いMCP要求と実機・GUI結合はM2に残す。

## 11. 完了条件

- [x] 対象と対象外を確定した
- [x] 既存試験を改訂し、修正と構造整理を検証した
- [x] API文書・仕様・品質gateを更新した
- [x] 再レビューを行い、未確認事項を引き継いだ
