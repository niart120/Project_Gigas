# 入力の生成保証と検査所有の整理

## 1. 目的・状態

状態: complete。[unit_063](../unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)第6節の単位3。InputState/InputStepの生成時保証を維持し、parserと生成後の重複検査を削った。I1は0c6ef5d、I2は3a79b9bで完了した。

## 2. 対象範囲・対象外

JSONの形式読取、共通不変条件の生成時保証、開始前の全列機器能力検査、encoderの前提、対応する公開API・試験を揃える。新しい入力型・検証済み型、検査省略コンストラクタ、検証状態・世代は作らない。機器別byte列、I/O所有、停止・解除の振る舞いは変えない。SerialConfig再解決は親M2の別作業。

## 3. 前提・関連文書

保存の単位1・2は完了。[入力の正本](../../initial/03-game-io.md)、[公開API](../../../docs/input-api.md)、unit_063第5.2節を正本とする。入力の生成時保証とJSON形式の型確認が少し重なることは許容し、値域・重複・press/waitの意味を再判定しない。

## 4. 振る舞い

InputStateは不変tuple・要素型・値域・ボタン重複と反対方向を生成時に確認する。InputStepは正の整数時間・kind・InputState型・press非neutral/wait neutralを確認し、State内部を再検査しない。未知ボタン名の機器適合は生成時条件に含めない。

parserは非空JSON配列から全要素を生成し、元JSONに依存しないtupleを返す。protocol引数を廃止し、座標・時間の値域は生成処理に任せる。生成失敗はInvalidSequenceErrorとなり、MCPではINVALID_SEQUENCEで入力開始前に返す。

InputServiceは非空tupleを受ける前提とし、空列の再検査を削る。使用可能な機器・BUSY・全列能力検査を同期的に行ってからtaskを登録する。encoderとSerialInput.sendは機器適合済みのInputStateを受け、能力を再検査しない。単独呼出しで前提を破った場合の拒否保証は廃止する。既存validate_stateは能力確認だけの関数として残す。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | I1: 直接構築とJSON解析が同じ共通条件を保証し、非空で不変な全列だけを外部入口から渡す | regression | unit/integration | 共通条件の試験を生成境界へ整理。parserのprotocol引数・重複値域とサービスの空列再検査を削除 |
| refactor-done | I2: 未知・非対応入力は列全体を開始前に拒否し、適合する状態を再検査せず既存byte列へ変換する | characterization | unit/integration | 単独encoderの能力拒否期待を削除。全列拒否・接続時neutral・停止解除を区別 |

## 6. 実装順と対象ファイル

I1でmodels.py、InputService.startと呼出側・対応試験を整理し、I2でprotocol.pyの再検査を削る。共通不変条件を先に試験し、機器能力を開始前に拒否する根拠を残す。docs/input-api、initial/03とdocstringを各段階のAPIに揃える。

## 7. 検証

入力protocol・serialのunit、InputService・MCP・保存入力のintegration、ruff format/check、ty、lock/build、文書参照・diffを実施する。byte列・停止回収を変更しないため実機を必須とせず、模擬I/O・実HTTPの受入と区別する。

I1の基準は、共通条件を構築境界へ整理し、未知ボタン・非対応touchの全列拒否と、外部の空列・後方生成失敗を追加した105 passed。既存の生成保証を維持するため、人工的なredは作らなかった。重複検査削除後、`uv run pytest tests/unit/test_input_protocols.py tests/unit/test_serial_input.py tests/integration/test_input_service.py tests/integration/test_game_io_mcp.py tests/integration/test_input_observation_path.py tests/integration/test_observation_storage.py -q`は172 passed。原JSONからの独立・生成後の不変性・全列拒否・保存入力の非再解釈・機器別byte列を確認した。

I1のgateは全体ruff format（242 files）・ruff check・ty、lock/build成功。公開parserは一引数へ変更し、型付き呼出側・docstring・公開例を揃えた。新しいAny・cast・ignore・依存は追加していない。変更文書のローカル参照33件・unit番号66件と差分を確認した。

I2は単独encoderの能力拒否3例を削り、I1と同じ対象の169試験が成功した。能力検査の製品呼出しはInputService開始時だけと静的確認し、生成境界・設定境界・実際のI/O失敗の検査は維持した。全体の`uv run pytest tests/unit tests/integration -q`は803 passed（65.59秒）。ruff format/check・ty・lock/buildも成功し、単位4の保存・入力結合の受入を兼ねる。GUIからの実機通し操作・他機器・電源断・性能の新規実測は行っていない。

## 8. 先送り事項

親M2の小整理はunit_054が所有する。保存・入力の全体gateはunit_063第6節の単位4として実施済み。

## 9. 完了チェックリスト

- [x] 生成時保証・機器適合・呼出前提を分けた
- [x] I1・I2と公開APIの改訂を完了した
- [x] 検証結果と参照先を更新した
