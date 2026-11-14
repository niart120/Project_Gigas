# M2 接続・取得境界の残件整理

## 1. 目的・状態

状態: complete。[unit_054](../unit_054/KISS_SIMPLIFICATION.md) M2の残件を処置した。[unit_063](../../complete/unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)の保存・入力整理と全体gate通過後に着手し、R1〜R3を別commitで完了した。

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
| refactor-done | R1: 解決済み設定で現在のportを照合し、接続と初期neutralを行う | characterization | unit | c2cabaa。設定・接続の37試験と静的検査成功 |
| refactor-done | R2: 取得要求を一度取り出し、待機と取得の終了を回収する | characterization | integration | 6ffd9f8。取得関連47試験と静的検査成功 |
| refactor-done | R3: 起動したprotocolで通知を受け、EOF・起動取消し・close時に処理を回収する | characterization | integration | 6fa7e8d。transport 22試験と静的検査成功 |

## 6. 対象ファイル

`src/gigas/game_io/input/serial.py`、`src/gigas/game_io/capture/service.py`、`src/gigas/runtime/codex/transport.py`。対応するserial unit、capture service/fallback/failures/configuration、codex transport integrationは、意味のある既存試験を維持する。分岐や関数の呼出回数だけを固定するテストは作らない。

## 7. 検証

各itemで対応試験とty、ruffを実行する。最後に全unit/integration、ruff format/check、ty、lock/build、仕様・公開文書の参照・diffを確認し、親M2の受入とする。実機・実app-serverを新たに実行する変更ではなく、模擬プロセス・I/Oの結果として報告する。

R1は既存のserial 10試験を変更前の基準として確認した。構造整理なので人工的なredや検査呼出回数の試験は追加せず、`_open_native`の再解決とimportだけを削除した。`uv run pytest tests/unit/test_serial_input.py tests/unit/test_settings.py -q`は37 passed。設定解決、現在のport照合、接続時neutral、送信失敗・取消し回収を確認し、対象ruff format/checkと全体tyも成功した。SerialInputの解決済み設定という前提は既存docstringを維持し、公開API文書へ明記した。

R2は`uv run pytest tests/integration/test_capture_service.py tests/integration/test_capture_fallback.py tests/integration/test_capture_failures.py tests/integration/test_capture_configuration.py -q`が変更前47 passed（20.49秒）、変更後47 passed（20.48秒）。Conditionの待機・要求取出しを同じ制御フローへ揃え、取出し後の到達不能なNone分岐を削った。列挙・画像取得・再接続・起動前shutdown・取得中の終了と故障時回収を既存試験で確認し、対象ruff format/check、全体ty、差分検査も成功した。新しい状態・assert/cast/ignore・構造依存の試験は追加していない。

R3は`uv run pytest tests/integration/test_codex_transport.py -q`が変更前後とも22 passed（各1.47秒）。subprocess起動で得たprotocolをreaderの引数へ渡し、未設定なら黙って終了する分岐を削った。模擬子プロセスで通知・応答の配送、EOF、不正frame、起動取消し、close、子孫がpipeを保持した場合の回収を確認した。対象ruff format/check、全体ty、差分検査も成功し、新しい状態・型検査の抑制・public APIの変更はない。

最終製品commit `6fa7e8d`で全unit/integrationは803 passed（66.37秒）。sync・lock、全体ruff format/check・ty、build、独立環境の配布物・起動確認も成功した。command・確認範囲・配布確認の初回環境エラーは親unit_054第9節へ集約する。

## 8. 先送り事項

none。unit_054で既にU08/U10等へ割り当てた長時間・通常復帰の機能を本作業へ取り込まない。

## 9. 完了チェックリスト

- [x] 保存・入力の完了と残件の範囲を確認した
- [x] R1〜R3の整理と対応試験を完了した
- [x] 最終gateと親unit_054の完了条件を確認した
