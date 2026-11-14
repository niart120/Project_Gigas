# 保存の読込・新規記録の検査縮小

## 1. 目的・状態・起点

状態: complete。[unit_063](../unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)第6節の最初の作業単位を完了した。既存の記録を保持して追記し、生成済み対話・観測を内容の再検査なしで保存する。

## 2. 対象範囲

読込時のID・時刻検査縮小、新規記録の内容検査削除、画像保存先の保全、JSON化失敗の保存エラー化。対応する仕様・公開API・試験を同時更新する。

## 3. 対象外

flush/fsyncの撤去、入力検証、所有・回収・保存形式の変更、自動修復・再送。

## 4. 関連文書・着手条件

[親unit_054](../../complete/unit_054/KISS_SIMPLIFICATION.md)、[保存の正本](../../initial/04-agent-memory.md)、[公開API](../../../docs/recorded-observation.md)。M1と会話再編は完了し、unit_063の保存方針は合意済み。現行コード・対応試験を確認し、ユーザーの実装指示に従って着手した。

## 5. 振る舞い

読込では非空project ID・対応schema・保存先・共通行形式・追記順を残し、project IDのUUID表記、作成時刻、event ID・発生時刻の検査を削る。新規生成の形式は維持する。

新規保存では対話・観測内容を再検査せず、画像IDのobs-UUID4制約から保存先を作る。JSON化・UTF-8変換のTypeError/ValueErrorはProjectErrorとし、JSONLに触れず次sequenceも進めない。既に公開した画像は保持する。writerの原本保全・排他・結果不明時の追記拒否を維持する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | S1: ID表記・時刻に依存せず既存projectを読み、原本保持と正しい次採番で追記する | regression | integration | 共通項目不正の拒否は残す。読込検査だけを縮小し、別の構造変更は不要 |
| refactor-skipped | S2: 行データの生成失敗をProjectErrorとして報告し、原本・採番を維持して次の保存を受け付ける | edge | integration | 共通の行データ作成で確認。観測では公開済み画像を保持。別の構造変更は不要 |
| refactor-done | S3: 新規内容を再検査せず保存し、画像保存先の逸脱・既存画像上書きを拒否する | regression | integration | 内容検査と二重JSON化を削除。S2の共通エラー変換を維持 |

## 7. 実装方針

S1→S2→S3の順に進める。対話の事前JSON化を削る前に、S2で共通の行データ作成にあるTypeError/ValueErrorの変換を確認する。S3で事前検査と二重JSON化を削り、一度だけ直列化する構成へ揃える。新しいvalidator・dataclass・状態は追加しない。各itemを検証して別commitにする。

## 8. 対象ファイル

`src/gigas/storage/project.py`、`tests/integration/test_observation_storage.py`、initial/04、recorded-observation。共通保存の回収は既存`test_observation_service.py`で確認する。

## 9. 検証

事前準備の記録: `uv sync --dev`・`uv lock --check`成功。設計レビューの文書検査はunit_063第9節へ記録した。完了時に対応integration、ruff、ty、lock/build、文書参照・diffを確認する。実機・電源断試験は対象外。

S1: `uv run pytest tests/integration/test_observation_storage.py -q -k 'open_preserves_unknown_events_fields_and_sequence_gaps or open_requires_nonempty_project_identity or invalid_event_envelope'`は新しい読込例2件がUUID検査で失敗し、残す条件12件は成功した。読込検査縮小後、同ファイル全体は53 passed。原本のmetadata・行、欠番後の採番、新規生成形式、排他・失敗保全を確認した。

S1の静的確認: 対象2 Pythonファイルのruff format/check、全体の`uv run ty check --no-progress`成功。変更3文書のローカル参照先31件とunit番号64件の一意性を確認した。

S2: `uv run pytest tests/integration/test_observation_storage.py -q -k row_encoding_failure`はUTF-8変換の例外がProjectErrorにならない2件でred、NaN拒否1件は既存処理で成功。共通のエラー変換後は同ファイル全体56 passed。原本・採番・公開済みPNG・次の保存を確認した。ruff formatの改行指摘を直し、ruff check・全体ty成功。

S3: 拒否保証の削除を、不正な内部値の必ず受理される保証へ置き換えるredは作らなかった。保存先逸脱拒否を加え、廃止するFrame識別値・空thread IDの拒否期待を削った基準は保存53 passed。内容検査と二重JSON化の削除後、`uv run pytest tests/integration/test_observation_storage.py tests/integration/test_observation_service.py tests/integration/test_input_observation_path.py -q`は72 passed。内容の生成、原本・画像の保全、受付済み保存の回収、保存失敗で入力結果を変えないことを確認した。

単位全体のgate: 対象Pythonのruff format/check、全体ty、`uv lock --check`、`uv build`成功。公開APIの前提・Raisesをdocstringと公開文書へ反映し、変更文書の参照先・差分を確認した。明示同期はまだ維持し、電源断・実機・性能の新しい検証結果はない。

## 10. 先送り

同期保証縮小と入力検証はunit_063の後続単位。小整理はunit_054の残件。

## 11. 完了チェックリスト

- [x] 対象範囲と合意した契約を確認した
- [x] TDD itemと対応試験を完了した（S1: 7a5f398、S2: 15ecaa3、S3: 976f8e4）
- [x] 公開仕様・docstringと検証結果を更新した
- [x] 親仕様と参照先を更新した
