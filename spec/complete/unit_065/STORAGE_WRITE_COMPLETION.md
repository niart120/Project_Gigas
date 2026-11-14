# 保存の完了条件とI/O失敗の整理

## 1. 目的・状態

状態: complete。[unit_063](../unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)第6節の単位2。metadata・PNG・JSONLの明示flush/fsyncを撤去し、通常のwrite/closeを保存の完了条件とした。実装commitは690a6a5。

## 2. 対象範囲・対象外

保存の同期保証と故障試験、公開文書を変更する。画像公開順、既存のバッファ方式、OSロック、受付済み保存の回収は維持する。新しいバッファ、修復、再送、transaction、rollback、電源断耐性・性能の保証は追加しない。

## 3. 前提・関連文書

[unit_064](../unit_064/STORAGE_VALIDATION_BOUNDARIES.md)の検査縮小は完了し、関連72試験と静的・package gateを確認済み。[保存の正本](../../initial/04-agent-memory.md)と[公開API](../../../docs/recorded-observation.md)を同時更新した。

## 4. 振る舞い

metadataは排他的作成、PNGは同じディレクトリの一時ファイルをcloseしてから上書きしないrename、JSONLは完全な行データを追記してcloseする。明示flush/fsyncは呼ばない。closeに伴うバッファ排出は通常のファイル処理へ委ねる。

画像公開前の失敗ではログと採番を進めず継続利用できる。JSONLのopen/write/close失敗・短いwriteでは原本と公開済み画像を保持して追記を止める。完全な行が残れば明示reloadで次番号を得て再開し、不完全行があれば拒否を維持する。どちらも自動再送はしない。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | W1: 通常の書込・closeで保存し、公開失敗・不完全追記・書込後のclose失敗で原本と追記可否を保つ | characterization | integration | fsync故障とイベント種別の総当たりを削り、実ファイルへの部分write・close時の模擬失敗を確認 |

## 6. 実装方針・対象ファイル

`src/gigas/storage/project.py`の明示同期だけを外す。`tests/integration/test_observation_storage.py`の故障注入を残すI/O境界へ合わせる。保存内容の検査は再導入しない。生成するファイル内容は同じであり、保証削除を新しい拒否・受理保証のテストへ置き換えない。

## 7. 検証

保存・RecordingService・入力観測結合の試験、ruff format/check、ty、lock/build、文書参照・diffを実施する。実機・電源断・性能試験は対象外。実績は実行後に記録する。

W1では、故障注入を実際の短いwriteとfile closeへ変更した基準が保存49 passed。明示同期の撤去後、`uv run pytest tests/integration/test_observation_storage.py tests/integration/test_observation_service.py tests/integration/test_input_observation_path.py -q`は68 passed。画像公開失敗時の一時ファイル回収、部分行によるreload拒否、完全行が残る場合のreload成功と採番、入力結果と保存失敗の独立を確認した。同期呼出回数を固定する代替テストは作らず、明示同期の削除は差分で確認した。

対象Pythonのruff format/check、全体ty、`uv lock --check`・`uv build`成功。変更3文書のローカル参照31件・unit番号65件と`git diff --check`を確認した。型検査が指摘した試験用contextmanagerの戻り値はGeneratorへ修正し、ignore/castは追加していない。

## 8. 先送り事項

入力検査はunit_063の単位3。親M2の小整理はunit_054が所有する。

## 9. 完了チェックリスト

- [x] 保存の完了条件と失う保証を確認した
- [x] 残すI/O失敗と正常保存・回収を検証した
- [x] 公開仕様・実績・参照を更新した
