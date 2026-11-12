# U05 観測・保存・入力境界の整理

## 1. 目的と状態

状態: **wip**。U05 M1レビューで確認した責務の混在と記録の不整合を修正する。起点は2026-11-12のユーザー依頼と、[M1](../../complete/unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)の実装。開始時は `2bcdbe4`、作業ツリーはclean。

## 2. 対象範囲

最新観測と事後観測の入口、共通記録窓口の生成・注入、保存形式と通信能力の検証、停止時の送出結果、後から判明するturn IDを整理する。既存試験の改訂を優先する。

## 3. 対象外

M2のGUI・MCP入力公開、実機受入、Bluetooth、追加の管理クラスやイベント基盤は扱わない。read開始時刻による事後画像の条件は変更しない。

## 4. 前提と関連文書

[初期実行管理](../../initial/02-runtime.md)、[ゲームI/O](../../initial/03-game-io.md)、[記録](../../initial/04-agent-memory.md)、[U05親](../unit_010/INPUT_AND_STOP.md)、[M0](../../complete/unit_036/INPUT_AND_STOP_DESIGN.md)に従う。基準となる既存試験602件、静的検査とビルドは成功済み。

## 5. 振る舞い仕様

観測サービスは最新画像のobserveと、操作後のobserve_afterを提供し、選定後の画像加工・保存依頼を共用する。独立したinput_observationモジュールは撤去する。

共通RecordingServiceは組立箇所で一度生成し、観測と入力へ渡す。入力サービスの構築によるcallback上書きや観測thread文脈の変更を行わない。保存失敗から停止への接続も組立側で明示する。終了は符号化終了、共通保存のdrain、writer解放の順を守る。

保存済み入力列の検査は共通入力形式だけを扱い、現在対応するprotocolを推測しない。実行可能な入力の検査は選択protocolに対して受付側で行う。

送信中の停止でも開始済みwriteの結果を回収し、全byte成功はpress・neutralとも送出済みとして記録する。残りの列は実行しない。run開始後に判明した実turn IDを同じrunへ対応付け、以後の操作・終端記録へ使う。古いrunからの通知で現在の文脈や操作権を変更しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | R01 保存した入力列は現在のprotocol候補に依存せず検査・読込できる | regression | unit/integration | 期待失敗2件→関連131件成功。既存保存試験で能力変更後の再読込を確認 |
| todo | R02 最新・事後観測と操作は共通保存を明示的に共有し、停止と保存の寿命を維持する | characterization | integration | 実事後観測を通るfixtureへ改訂 |
| todo | R03 press送信中の停止でも回収済み成功を結果と記録に残す | regression | integration | 既存neutral停止試験の対象を拡張 |
| todo | R04 run開始後に判明したturn IDを記録し、旧runの通知を拒否する | regression | integration | 操作記録試験を改訂 |

## 7. 設計メモ

新しい抽象化を設けず、既存サービス間の依存を明示する。開始記録成功、単一所有者、操作取消し、終了時のI/O回収、結果と画像の非同期保存は維持する。

## 8. 対象ファイル

`src/gigas/services/`、`game_io/input/models.py`、`storage/input_events.py`、対応する既存試験、公開docs、関連仕様。

## 9. 検証

各修正の期待失敗・成功と構造整理後の既存試験を確認する。最終gateはruff format/check、ty、unit/integration、uv lock --check、uv build、git diff --check。仕様・docsの参照と未確認表示を読む。独立レビューで責務と回帰を再確認する。

## 10. 未確認・先送り

read_started_nsの必須性を含む事後画像の条件自体の妥当性は再検討事項。今回は現契約を維持し、条件を変更する前にM0・初期03と試験の判定を合わせる。機器内部遅延・実ゲームへの反映は未検証。長いMCP要求と実機・GUI結合はM2に残す。

## 11. 完了条件

- [x] 対象と対象外を確定した
- [ ] 既存試験を改訂し、修正と構造整理を検証した
- [ ] API文書・仕様・品質gateを更新した
- [ ] 再レビューを行い、未確認事項を引き継いだ
