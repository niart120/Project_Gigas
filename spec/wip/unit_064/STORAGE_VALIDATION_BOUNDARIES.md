# 保存の読込・新規記録の検査縮小

## 1. 目的・状態・起点

状態: wip。ユーザーが合意した[unit_063](../unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)第6節の最初の実装単位。利用者は既存の記録を保持して追記し、生成済み対話・観測を内容の再検査なしで保存できる。

## 2. 対象範囲

読込時のID・時刻検査縮小、新規記録の内容検査削除、画像保存先の保全、JSON化失敗の保存エラー化。対応する仕様・公開API・試験を同時更新する。

## 3. 対象外

flush/fsyncの撤去、入力検証、所有・回収・保存形式の変更、自動修復・再送。

## 4. 関連文書・着手条件

[親unit_054](../unit_054/KISS_SIMPLIFICATION.md)、[保存の正本](../../initial/04-agent-memory.md)、[公開API](../../../docs/recorded-observation.md)。M1と会話再編は完了し、unit_063の保存方針は合意済み。現行コード・対応試験を確認して着手した。

## 5. 振る舞い

読込では非空project ID・対応schema・保存先・共通行形式・追記順を残し、project IDのUUID表記、作成時刻、event ID・発生時刻の検査を削る。新規生成の形式は維持する。

新規保存では対話・観測内容を再検査せず、画像IDのobs-UUID4制約から保存先を作る。JSON化・UTF-8変換のTypeError/ValueErrorはProjectErrorとし、JSONLに触れず次sequenceも進めない。既に公開した画像は保持する。writerの原本保全・排他・結果不明時の追記拒否を維持する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| todo | S1: ID表記・時刻に依存せず既存projectを読み、原本保持と正しい次採番で追記する | regression | integration | 共通項目不正の拒否は残す |
| todo | S2: 新規内容を再検査せず保存し、画像保存先の逸脱・既存画像上書きを拒否する | regression | integration | 内部値を人為的に壊した拒否試験は削除 |
| todo | S3: 行データの生成失敗をProjectErrorとして報告し、原本・採番を維持して次の保存を受け付ける | edge | integration | 観測では公開済み画像を保持 |

## 7. 実装方針

既存writerの中で不要な検査を削る。新しいvalidator・dataclass・状態は追加しない。各itemを検証して別commitにする。

## 8. 対象ファイル

`src/gigas/storage/project.py`、`tests/integration/test_observation_storage.py`、initial/04、recorded-observation。共通保存の回収は既存`test_observation_service.py`で確認する。

## 9. 検証

着手時: `uv sync --dev`・`uv lock --check`成功。製品変更のred/greenは未実行。完了時に対応integration、ruff、ty、lock/build、文書参照・diffを確認する。実機・電源断試験は対象外。

## 10. 先送り

同期保証縮小と入力検証はunit_063の後続単位。小整理はunit_054の残件。

## 11. 完了チェックリスト

- [x] 対象範囲と合意した契約を確認した
- [ ] TDD itemと対応試験を完了した
- [ ] 公開仕様・docstringと検証結果を更新した
- [ ] 親仕様と参照先を更新した
