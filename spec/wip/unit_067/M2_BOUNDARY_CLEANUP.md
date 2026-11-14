# M2 接続・取得境界の残件整理

## 1. 目的・状態

状態: wip。[unit_054](../unit_054/KISS_SIMPLIFICATION.md) M2の残件を処置する。[unit_063](../../complete/unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)の保存・入力整理と803件の全体試験、静的・package gateが完了したことを着手条件とする。

## 2. 対象範囲・対象外

解決済みSerialConfigの再解決、同じCondition内の重複pending確認、起動済みstdio readerのprotocol未設定分岐を削る。正常な設定・port照合・取得・通知・終了の振る舞いは維持する。SerialConfigを検査付きクラスへ変えず、None分岐をcast/ignore/assertへ置換しない。新しい状態・機器・UI・回復処理は対象外。

## 3. 関連文書と前提

[入力の正本](../../initial/03-game-io.md)、[実行管理](../../initial/02-runtime.md)、[入力API](../../../docs/input-api.md)。保存・入力から独立した小さい構造変更として実施し、各itemを別commitにする。

## 4. 振る舞い・実装方針

SerialInputは解決済み設定を受け、open時点のport一致と実際のopen結果を確認する。設定値の再解決だけを削る。

CaptureServiceはConditionのlock内で、終了か要求取出しが可能になるまで待つ。要求を取り出す分岐だけから実行へ進み、判定後に同じpendingをもう一度検査しない。待機中・要求ありのどちらでも終了要求を優先する。

StdioTransportはsubprocess起動で得たprotocolをreaderへ渡す。reader自身は未設定状態を持たず、現在のEOF・不正通知・切断・取消し回収を維持する。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| todo | R1: 解決済み設定で現在のportを照合し、接続と初期neutralを行う | characterization | unit | 既存の実ポート境界・送信・取消し試験で確認 |
| todo | R2: 取得要求を一度取り出し、待機と取得の終了を回収する | characterization | integration | 既存の列挙・取得・再接続・終了試験で確認 |
| todo | R3: 起動したprotocolで通知を受け、EOF・起動取消し・close時に処理を回収する | characterization | integration | 既存の模擬子プロセス試験で確認 |

## 6. 対象ファイル

`src/gigas/game_io/input/serial.py`、`src/gigas/game_io/capture/service.py`、`src/gigas/runtime/codex/transport.py`。対応するserial unit、capture service/fallback/failures/configuration、codex transport integrationは、意味のある既存試験を維持する。分岐や関数の呼出回数だけを固定するテストは作らない。

## 7. 検証

各itemで対応試験とty、ruffを実行する。最後に全unit/integration、ruff format/check、ty、lock/build、仕様・公開文書の参照・diffを確認し、親M2の受入とする。実機・実app-serverを新たに実行する変更ではなく、模擬プロセス・I/Oの結果として報告する。

## 8. 先送り事項

none。unit_054で既にU08/U10等へ割り当てた長時間・通常復帰の機能を本作業へ取り込まない。

## 9. 完了チェックリスト

- [x] 保存・入力の完了と残件の範囲を確認した
- [ ] R1〜R3の整理と対応試験を完了した
- [ ] 最終gateと親unit_054の完了条件を確認した
