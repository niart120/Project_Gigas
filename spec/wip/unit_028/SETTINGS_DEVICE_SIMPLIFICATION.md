# 設定・機器選択の単純化

## 1. 概要・起点

状態: **wip**。[unit_027 M1](../unit_027/IMPLEMENTATION_SIMPLIFICATION.md)の子仕様。2026-11-11のユーザー指示により、合意したM0の操作を実装する。既存状態に分岐を追加して温存せず、旧経路とそれを固定する試験を整理する。

## 2. 対象範囲

親仕様5.1〜5.5の設定・映像選択・保存・失敗・終了処理を対象とする。基準設定とフォーム編集値、単一の保存要求、取得側の最新反映先と実資源へ責務を整理する。モーダル、保存後に閉じるOK、未接続、処理中切替を結合する。再読込、独立した映像接続／停止、終了待ち取消し、旧保存helperを撤去する。機器欄の重複状態表示・非表示診断欄も、旧状態管理の撤去と一緒に整理する。

## 3. 対象外

全体UI/UX、回答カード、映像情報画面と専用FPS計測、旧公開`select_device()`の撤去は親M2へ残す。設定項目・タブ、Codexの認証・接続・中断契約、captureサービスの隔離・期限・解放確認は維持する。

## 4. 関連文書・着手条件

[親仕様](../unit_027/IMPLEMENTATION_SIMPLIFICATION.md)、[初期I/O設計](../../initial/03-game-io.md)、[実装計画](../../initial/05-implementation.md)、[前回実装記録](../../complete/unit_026/SETTINGS_AND_STATUS_REDESIGN.md)を参照する。M0の操作仕様は記録済み、製品実装の検証は本書で行う。

## 5. 振る舞いと試験の整理

操作契約の正本は親仕様5.1〜5.5とする。設定フォームの三重機器管理・接続待ちの閉じる予約・自動列挙予約を廃止する。保存は一件ずつ、映像反映先は取得所有者の最新一件とし、旧資源の解放確認を省略しない。

| 既存試験 | 判断 |
|---|---|
| `test_video_settings_gui` | 選択・一覧更新・値の保持を新契約へ書換え。保存機器と実取得先の二重候補／明示選択記憶の試験は削除。単なる表示設定の直積を縮小 |
| `test_settings_apply_contract`、`test_settings_accept`、`test_settings_source_menu` | 起動後適用、再試行、OKと接続成功の分離、モーダルを同じ試験へ反映。再読込・一時停止・モデルレス同期の旧契約は削除 |
| `test_video_application`、`test_resolution_application` | 保存失敗・旧解放・新接続失敗・要求条件の比較を維持。停止操作を未接続へ書換え、処理中の最新反映先を既存切替試験で確認 |
| `test_app_settings`、`test_app_lifetime`、`test_settings_recovery` | 保存・原本保護・実行中I/O・終了確認を維持。再読込と終了取消し専用の試験は削除し、適用／閉じるの操作へ整理 |
| メニュー・キーボード・列挙・通知の結合試験 | 残る操作経路へ更新。削除した属性・ボタンが存在しないことだけを検証する試験は作らない |

新規試験は既存試験の書換えでは表せない製品契約に限り、追加理由を記録する。件数の削減目標は置かない。削除した経路の不在検査、自然言語の固定文言検査、内部属性数の検査は追加しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | S1 フォームは単一の編集値を保持し、一覧更新で変更せず、保存中も閉じられる | regression | integration | 既存フォーム試験を書換え |
| refactor-skipped | S2 保存後の最新機器へ旧資源解放後に切替し、未接続は未開始の接続を取り消す | regression | integration | 既存取得制御・切替試験を書換え |
| todo | S3 モーダルのOK／適用は保存までを共有し、接続成否と閉じる操作を分離する | regression | integration | 既存適用・保存失敗・受理試験を書換え |
| todo | S4 設定中断・保存中の閉じる・アプリ終了で、I/Oとサービスの寿命を保つ | regression | integration | 既存終了・キーボード・復旧試験を整理 |

## 7. 設計メモ

M0の現行状態棚卸しを踏襲する。既存captureサービスの世代・解放結果を使い、汎用ジョブ管理や新しい自動復旧機構を作らない。保存要求の入力・適用範囲・OKの閉じる指定は一件の操作に束ね、ShellとVideoControllerに同じ接続予定を二重保持しない。

## 8. 対象ファイル

`src/gigas/app/shell.py`、`app/video.py`、`ui/settings_dialog.py`、`ui/main_window.py`、`ui/video_panel.py`、必要な通知・確認部品、対応する既存試験と`docs/`。依存・package metadata・captureドライバーは変更しない。

## 9. 検証

各項目のred／greenと整理結果を以下へ追記する。最終gateはruff format/check、ty、unit/integration、uv lock --check、uv build、git diff --check。公開API・docstring・利用者docsの整合、代表画面のモーダル／保存失敗／中断導線を確認する。実機・外部Codex接続は未実施なら明記する。

開始時: `codex/implementation-scope-review`。M0までの未コミット文書を保持。製品変更前の試験・型検査結果は未取得。

### S1 フォームの編集値

`uv run pytest tests/integration/test_video_settings_gui.py -q --tb=short`: 既存試験を新契約へ書換え、モーダルと保存中閉じるの2件red／6件passから8件pass。二重候補・選択意図専用の試験と表示値の重複直積を整理し、22ケースから8ケースへ削減。フォームの三重機器値、接続target API、閉じる禁止状態、非表示診断欄を撤去した。refactor-skipped（状態撤去は今回の振る舞い変更そのもの）。Shellの旧呼出しはS3で置換予定のため、全体試験はこの段階で実施しない。

### S2 最新の映像反映先

`uv run pytest tests/integration/test_video_application.py -q -k 'latest_target or reapplying_same' --tb=short`: 既存の一時停止・接続経路の2試験を、処理中の未接続／次機器への置換と同条件再適用へ書換え。重複切断・再接続による3件redから3件pass。最新反映先だけを保持し、接続／停止action、自動列挙予約、操作可否の重複状態を撤去。refactor-skipped。保存・失敗・実spawnの既存回帰はShell結合後に同じファイルで検証する。

## 10. 先送り

親M2の映像情報・専用計測・回答カード・旧公開機器helper。全体UI/UXはジャーナルの別議論、物理入力の即時停止はU05。

## 11. 完了チェックリスト

- [x] 操作契約と既存試験の書換え・削除方針を記録した
- [ ] S1〜S4を実装し、不要状態・旧経路を撤去した
- [ ] 対応する既存試験・利用者docsを更新した
- [ ] 品質gate・代表画面・未検証事項を記録した
