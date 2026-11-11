# 設定・機器選択の単純化

## 1. 概要・起点

状態: **complete**。[unit_027 M1](../../wip/unit_027/IMPLEMENTATION_SIMPLIFICATION.md)の子仕様。2026-11-11のユーザー指示により、合意したM0の操作を実装する。既存状態に分岐を追加して温存せず、旧経路とそれを固定する試験を整理する。

## 2. 対象範囲

親仕様5.1〜5.5の設定・映像選択・保存・失敗・終了処理を対象とする。基準設定とフォーム編集値、単一の保存要求、取得側の最新反映先と実資源へ責務を整理する。モーダル、保存後に閉じるOK、未接続、処理中切替を結合する。再読込、独立した映像接続／停止、終了待ち取消し、旧保存helperを撤去する。機器欄の重複状態表示・非表示診断欄も、旧状態管理の撤去と一緒に整理する。

## 3. 対象外

全体UI/UX、回答カード、映像情報画面と専用FPS計測、旧公開`select_device()`の撤去は親M2へ残す。設定項目・タブ、Codexの認証・接続・中断契約、captureサービスの隔離・期限・解放確認は維持する。

## 4. 関連文書・着手条件

[親仕様](../../wip/unit_027/IMPLEMENTATION_SIMPLIFICATION.md)、[初期I/O設計](../../initial/03-game-io.md)、[実装計画](../../initial/05-implementation.md)、[前回実装記録](../unit_026/SETTINGS_AND_STATUS_REDESIGN.md)を参照する。M0の操作仕様は記録済み、製品実装の検証は本書で行う。

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
| refactor-skipped | S3 モーダルのOK／適用は保存までを共有し、接続成否と閉じる操作を分離する | regression | integration | 既存適用・保存失敗・受理試験を書換え |
| refactor-done | S4 設定中断・保存中の閉じる・アプリ終了で、I/Oとサービスの寿命を保つ | regression | integration | 既存終了・キーボード・復旧試験を整理 |
| refactor-skipped | S5 設定パスの隣から保存フォルダーを開き、失敗詳細を確認できる | regression | integration | 追加指示による入口移動。既存復旧試験を書換え、メニュー配線だけの試験を削除 |

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

### S3 保存と映像反映の結合

既存適用・OK試験8件を先に書換え、旧action参照による結合未対応を確認。Shellを単一のSettingsRequestへ置換し、メニュー同期・接続待ち閉じる・再読込・旧save経路を撤去した。保存ボタンsignalのboolが設定引数へ入る不具合による2件失敗を修正し、8件pass。取得・解放失敗、原本保護、実spawn、通知詳細を含む関連7ファイルの既存試験は42件pass（`uv run pytest`、`-q --tb=short`）。新規試験ファイルは追加せず、旧ラベル・一時停止・モデルレス同期だけの試験を削除した。設定から開く通知詳細はモーダルの子として到達可能にし、重複する設定ボタンを撤去。refactor-skipped。

### S4 寿命・既存試験の整理

既存の終了・保存・キーボード・通知試験を、新しい適用／閉じる操作へ書き換えた。最初の全体試験は4件失敗／511件成功。旧内部属性への注入、終了待ち取消し、モデルレスの再フォーカス、旧pendingの検査を整理し、関連30件が成功。成功後、エラー文言の重複保持と不使用プロパティを除去し、開始済みの反映先を予約欄から消した（refactor-done）。最終の`uv run pytest tests/unit tests/integration -q --tb=short`は513件成功（37.23秒）。変更前556件から43件減り、新しい試験ファイルや撤去機能の不在だけを調べる試験は追加していない。

`uv lock --check`、`uv run ruff format --check .`（167ファイル）、`uv run ruff check .`、`uv run ty check --no-progress`、`uv build`、`git diff --check`が成功。Windows Qtの模擬機器プローブでは、設定のモーダル化、保存失敗時の入力保持、子の詳細画面、保存中Esc、閉じた後のCodex中断、保存後の初回フレーム前の接続開始を確認した。設定・詳細画面の画像も確認した。実機映像・外部Codex接続は今回未実施。

### 追加指示：設定フォルダーへの入口

ユーザー指示により「設定ファイルの場所を開く」を主画面メニューから設定の一般タブへ移す。既存の読み取り専用・コピー可能なパス欄の隣に「フォルダを開く」を置く。ファイル選択による保存先切替は追加しない。保存中・読込失敗中も場所は参照でき、開けない場合は同じ設定画面から詳細を確認できる。元の機能は前回改修の`fb596fc`で導入されたもの。

### S5 入口の移動と最終確認

既存のフォルダー操作・失敗詳細試験を新しいボタンへ書き換え、未実装の入口による3件失敗／2件成功から5件成功を確認した。旧メニューの配線だけを確認する1件は削除。refactor-skipped（入口移動だけで独立した構造整理は不要）。`uv run pytest tests/integration/test_settings_recovery.py tests/integration/test_main_menus.py tests/integration/test_keyboard_workflow.py tests/integration/test_video_settings_gui.py -q --tb=short`は38件成功。全体513件成功後の変更に対応する範囲を再検証した。現在の収集件数は512件（開始時556件から44件減）。

追加変更後もruff format/check、ty、build、diff --checkが成功。Windows Qtで一般タブを表示し、パスとボタンの配置、編集禁止中も場所を開けることを確認した。OSのフォルダー起動は既存のQDesktopServices呼出しを維持し、試験では成功／失敗を代替応答で確認した。実Explorer起動は未実施。

自己レビューでは、公開メソッドの型・docstringと利用者向けdocsを照合した。互換用の旧経路やAny／type ignoreは追加せず、captureの公開サービス契約・依存・パッケージメタデータは維持。文書のローカル参照、unitの一意性、仮テキストを確認した。全体UI/UXとM2へ範囲を広げていない。

## 10. 先送り

親M2の映像情報・専用計測・回答カード・旧公開機器helper。全体UI/UXはジャーナルの別議論、物理入力の即時停止はU05。

## 11. 完了チェックリスト

- [x] 操作契約と既存試験の書換え・削除方針を記録した
- [x] S1〜S5を実装し、不要状態・旧経路を撤去した
- [x] 対応する既存試験・利用者docsを更新した
- [x] 品質gate・代表画面・未検証事項を記録した
