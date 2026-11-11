# 映像情報・回答カード・残存コードの整理

## 1. 概要・起点

状態: **wip**。[unit_027 M2](../unit_027/IMPLEMENTATION_SIMPLIFICATION.md)の子仕様。2026-11-11のM2着手指示により、親5.6節の残項目を実装する。M1は[unit_028](../../complete/unit_028/SETTINGS_DEVICE_SIMPLIFICATION.md)で完了。既存試験の書換え・削除を優先し、撤去した機能の不在だけを検査する試験は追加しない。

## 2. 対象範囲

正常時の映像情報画面、機器報告FPSと実測FPSの取得・通信・保持、不使用の公開`select_device()`を撤去する。回答カードの手動折りたたみをなくし、処理後は短い結果を表示して、次の通常ターンの開始確認時に処理済みカードを片付ける。対応する既存試験と利用者docsを整理する。

## 3. 対象外

全体UI/UX、会話履歴の保存・転記、Goalや計画的終了の回答UI、MCP、物理入力は追加しない。映像取得・表示のFPS設定、画像の時刻・連番・世代、取得隔離・期限・解放確認、失敗通知と診断を維持する。M1の操作契約を変更しない。

## 4. 関連文書・着手条件

[設計概要](../../initial/01-overview.md)、[ランタイム](../../initial/02-runtime.md)、[I/O](../../initial/03-game-io.md)、[工程](../../initial/05-implementation.md)を正本とする。着手時は`codex/implementation-scope-review`、`6dfbb28`、作業ツリーはclean。収集済み試験は512件。

## 5. 振る舞い仕様

| 対象 | 契約 |
|---|---|
| 映像 | 主画面の実状態と失敗詳細を維持。正常時情報画面とFPSの専用計測・報告は提供しない。プレビュー頻度の制御と取得要求FPSは残す |
| 回答中 | 入力・判断対象を表示し、手動では折りたたまない。送信中は二重回答を禁止し、秘密入力を消去する |
| 処理後 | `sent`・`not_sent`・`unknown`・失効の短い結果を表示する。ターン終了直後は残し、質問・入力欄を再展開する操作は設けない |
| 次ターン | 実行側が新ターンのIDを受理した時点で、以前のターンの処理済みカードを取り除く。送信ボタン、開始要求、開始失敗だけでは取り除かない |
| 遅延・競合 | 未解決・送信中のカードは表示の都合で失効させない。次ターン開始後に届いた送信結果も照合し、その結果は次の整理境界まで表示する。古い世代の通知、重複開始通知で現在のカードを壊さない |

開始確認は既存のターンID受理箇所から型付きイベントとして通知する。GUIで接続状態から推測したり、カード撤去用に送信待ちフラグを持ったりしない。開始応答より先の質問・完了通知も同じ境界を通る。新しい状態機械やカード履歴は作らない。

## 6. TDD Test List・既存試験の整理

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | R1 映像の取得・失敗・解放を維持して、情報画面と専用計測・旧選択APIを撤去する | regression | unit / integration | 専用試験を削除し、取得の既存試験から計測assertだけを除く。削除そのもののredは作らない |
| todo | R2 回答処理後は短い結果へ移り、二重送信防止と秘密入力の消去を維持する | regression | integration | 再展開試験を結果表示へ書換え、手動折りたたみ専用試験を削除 |
| todo | R3 次ターンの開始確認で処理済みカードを片付け、失敗・遅延結果を保持する | regression | unit / integration | 既存の開始競合・カード寿命試験を拡張／統合 |

整理対象は`test_video_information`、`test_capture_models`の旧helper試験、取得backend/service/protocolの計測部分、回答layout/lifecycle/GUIの重複観点。異なる障害・公開契約の試験を、件数削減だけのために一つの巨大試験へまとめない。全体の件数と削除理由を最後に記録する。

## 7. 設計メモ

専用計測の撤去はcapture workerの`opened`通知まで追って削る。最初の画像の受理と起動期限は既存の連番・`started`通知で管理する。失敗診断用backend・diagnosticsは残す。旧`select_device`は製品呼出しがなく、実取得の機器解決は`select_candidates`が所有している。互換stubは残さない。

## 8. 対象ファイル

`src/gigas/app/video.py`、`ui/video_panel.py`・映像情報画面・メニュー、`game_io/capture`、`ui/request_card.py`・`connection_panel.py`、ランタイムの開始確認イベント、対応する既存試験、`docs/video*`・`docs/codex-gui.md`。

## 9. 検証

各項目で対応する既存試験を実行し、最後にunit/integration全体、ruff format/check、ty、lock整合、build、差分を確認する。公開APIと利用者docs、型境界、ローカルMarkdown参照をレビューする。Windows Qtで回答中／結果表示と次ターンの代表画面を確認する。実機・外部Codex接続は今回未実施なら明記する。

## 10. 先送り

R1検証: 映像情報専用の試験ファイルと旧`select_device`の6ケースを削除。取得試験は画像更新・再接続の契約を残してFPSのassertを除いた。`uv run pytest tests/unit/test_capture_models.py tests/unit/test_capture_backend.py tests/unit/test_capture_transport.py tests/integration/test_capture_service.py tests/integration/test_capture_fallback.py tests/integration/test_main_menus.py tests/integration/test_video_gui.py -q --tb=short`は85件成功（12.27秒）。`test_capture_platforms.py`は13件成功。tyで残っていた旧callback引数を見つけて削除し、型検査・ruff・差分検査が成功。撤去後の不在を調べる試験は作らず、削除に伴う状態縮小を振る舞い変更と一緒に行ったためrefactor-skipped。

全体UI/UXは実装整理後の別議論。U06でGoal・回答履歴の設計、U07で終了中の操作、U05で物理入力の即時停止を扱う。

## 11. 完了チェックリスト

- [x] 合意範囲と対象外、既存試験の整理方針を記録した
- [ ] R1〜R3を実装・検証した
- [ ] 残存経路・重複試験を確認し、docsを更新した
- [ ] 品質gate・実画面・未検証事項を記録した
