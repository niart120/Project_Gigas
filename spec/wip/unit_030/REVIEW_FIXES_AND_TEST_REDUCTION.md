# M1・M2レビュー修正とテスト削減

## 1. 概要・起点

状態: wip。2026-11-11のユーザー指示により、[レビュー](../../complete/unit_027/M1_M2_REVIEW.md)のR1〜R3とテスト見直し8件へ対応する。追加回答で、前回CSVの価値「中」107件は、検証が減ることを承知で一律削除する方針が確定した。

## 2. 対象範囲

未生成取消後の次要求、機器一覧の不要な再構築、エラーの案内先を修正する。前回の[472件のCSV](../../complete/unit_027/M1_M2_TEST_INVENTORY.csv)を削除対象の固定基準とし、同じ試験を改名して追加し直さない。見直し8件と重なる項目は削除で扱う。

## 3. 対象外

全体UI/UX、U04以降、新しい状態フラグ・予約・復旧機構、実機の再受入。テスト削除によって製品の既存機能を削除することはしない。

## 4. 関連文書・着手条件

[整理親仕様](../../complete/unit_027/IMPLEMENTATION_SIMPLIFICATION.md)、[M1](../../complete/unit_028/SETTINGS_DEVICE_SIMPLIFICATION.md)、[M2](../../complete/unit_029/DISPLAY_AND_REMAINDER_SIMPLIFICATION.md)、[取得設計](../../initial/03-game-io.md)、[工程](../../initial/05-implementation.md)。開始branchはcodex/implementation-scope-review。既存の未commit変更は前回レビュー文書3件だけで、これを保全する。

## 5. 振る舞い仕様

| 入力・状態 | 期待する結果 |
|---|---|
| worker生成前の要求を別機器／未接続で置換 | サービスがidleかつ解放未確認でなければ最新一件へ進む。資源が未作成なら解放の実績を要求しない |
| cleanupがunconfirmed | 新取得を開始せず、既存の所有権・失敗診断を保持 |
| 同じ機器一覧のまま初回画像が到着 | 候補を作り直さず、確定前の選択操作を維持 |
| 一覧が実際に変化 | 編集値を保持して候補を更新。検索／失敗表示は一覧の変化と独立して更新 |
| 取得／適用失敗 | 設定での機器再選択・再適用と、通知詳細へ案内。設定内の接続状態へ誘導しない |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | R1: 未生成取消から最新機器／未接続へ進める | regression | integration | 既存のlatest target試験を実サービス境界へ書換え、unreleasedの誤期待を削除。2件red→関連45件green |
| refactor-done | R2: 初回画像で候補のキー選択を戻さない | regression | integration | 残す機器発見GUI試験でred→5件green。通知経路整理後28件green |
| todo | R3: 失敗時の案内先と現UIを一致させる | review | docs/UI | 固定文言テストは作らない |
| todo | テスト整理: 中107件と見直し8件を整理 | refactor | tests | 107件の削除をnodeidで照合。低の重複、製品外の確認も整理 |

## 7. 設計判断

資源所有者のidleとunconfirmedを判断に使い、GUIに未spawn専用の状態を足さない。設定の候補は既存の_devicesとの比較で変化した時だけ再構築する。メニュー描画の重複呼出も整理する。

107件の一律削除は利用者の明示判断であり、不要な試験だけだったという評価へ変更しない。今回のR1/R2は残す「高」の既存試験で検証する。元CSVはレビュー時点の記録として変更しない。

## 8. 対象ファイル

src/gigas/app/video.py、shell.py、ui/settings_dialog.py、対応する既存tests、docs/video-api.md、レビュー追補と本仕様。削除対象一覧・結果は本仕様へ記録する。

## 9. 検証計画・結果

R1/R2は既存試験のred/greenを確認する。最後に収集nodeidとの差分、unit/integration全体、ruff format/check、ty、lock、build、Markdown参照とgit diff --checkを確認する。Windowsの代表操作は局所プローブで確認する。実機・外部Codexはnot run予定。

R1: `uv run pytest tests/integration/test_video_application.py -k latest_target -q --tb=short`は2件ともfailedとcompleteの差でred。判定分岐を削除後、`uv run pytest tests/integration/test_video_application.py tests/integration/test_capture_failures.py tests/integration/test_capture_fallback.py -q --tb=short`は45件成功（31.25秒）。既存の終了未確認・フォールバック制約も通過。APIのcleanup説明をdocstring-styleで実装と照合し、未生成取消の意味を追記した。

R2: `test_streaming_settings_use_cached_devices_without_interrupting_preview`を初回フレーム到着中のキー選択へ書換え、確定値がpath-bへ戻ることでredを確認。機器候補の再構築を変更時だけにしてdevice_discovery_guiの5件成功。green後に専用devices_changed通知を撤去し、一覧revisionも既存changedの通知判定へ統合。shellの同一イベント中のメニュー描画を一回にした。`uv run pytest tests/integration/test_device_discovery_gui.py tests/integration/test_video_application.py tests/integration/test_main_menus.py -q --tb=short`は28件成功（3.21秒）。

## 10. 先送り

全体UI/UXは別議論。削除したテストの復活や代替テストの作成を後続の自動作業として予約しない。

## 11. 完了チェックリスト

- [x] 対象範囲・削除基準を確認した
- [ ] R1〜R3を修正し検証した
- [ ] 107件と見直し8件の扱い・最終件数を記録した
- [ ] gateと未検証事項を記録した
