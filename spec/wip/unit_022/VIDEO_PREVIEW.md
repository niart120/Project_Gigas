# U03 M2 映像設定とプレビュー結合

## 1. 目的・状態・起点

状態: **wip（設定からTDD着手）**。[U03](../unit_008/VIDEO_CAPTURE.md)と[M0](../../complete/unit_020/VIDEO_CAPTURE_DESIGN.md)に従い、選択・接続・最新映像の表示・切断・終了をGUIへ結合する。

利用者は設定画面で機器の一覧を更新し、機器とモードを選んで保存する。主画面で接続し、同じ取得元から最新映像を表示する。

## 2. 対象範囲・対象外

TOML設定の追加、機器一覧からの選択、縦横比を維持する表示、非同期の取得サービスとの結合、終了と代表画面確認が対象。画像保存・MCPはU04、ゲーム入力・即時停止はU05。模擬画像は試験のみに使う。

## 3. 前提・工程内の分割

[M1](../unit_021/VIDEO_CAPTURE_SERVICE.md)の取得基盤とGUIを結合する。モード設定の保存・編集はM0の確定契約にだけ依存するためM1と並行できる。取得サービスへの結合・受入はM1実装後に進む。2026-11-11のユーザー指示で実機取得は準備完了の連絡まで保留し、機器に依存しない実装・試験を進める。

## 4. 振る舞い仕様

- `capture.width=1920`、`height=1080`、`fps=60`、`preview_fps=30`を省略時既定値とする。正整数のみ、width 16〜3840、height 16〜2160、fps 1〜120、preview_fps 1〜60。既存device文字列は破壊・自動解釈しない。
- 設定画面に候補一覧と更新操作を追加し、選択するとdevice欄へDevicePathを設定する。同名候補は表示で区別する。列挙の成功を接続済みとして扱わない。保存は既存の明示操作だけで行う。
- 主画面の接続は最後に読込・保存成功した設定をsnapshotとして使う。設定の編集・保存・再読込だけでは接続中の機器・モードを変えない。
- GUIタイマーは設定した表示頻度で最新Frameを読み、同世代・同連番を再変換しない。中間フレームをキューへ積まない。BGRからQImageのコピーを作り、paintで縦横比を保って余白中央へ表示する。共有Frameを変更しない。
- 状態・要求モード・実寸法・FPS報告値と実測値を短いラベルと値で表示する。接続開始・切断・取得失敗で画像を消去し、理由を表示する。説明文や説明用ツールチップは置かない。
- 接続開始と一覧更新は重複受付しない。設定読込失敗・保存中でも既存接続の切断を受付ける。アプリ終了では映像を停止し、GUIを応答可能に保ちながらワーカーと子の終了確認を待つ。終了未確認を正常終了にしない。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | P1 映像モードを検査し既定値・コメント付きTOMLへ保存する | new | unit/integration | red: 属性不存在・不正型を拒否しない14失敗。green: 下記検証。独立した4数値の変換のみで構造変更不要 |
| refactor-skipped | P2 機器を選んでモードとともに明示保存する | new | integration | red: モード消失・選択部品未実装。green: 25件。設定選択の同期を局所化し、大きい構造変更なし |
| todo | P3 最新画像をコピーし実画像の比率で描画する | new | integration | 上書き・同じ連番・消去・画素と余白 |
| todo | P4 設定snapshotで接続し遅い表示・切断・終了を結合する | new | integration | 実サービス模擬子、設定失敗中切断、GUI応答 |

各itemをred→green→構造整理判断→検証→コミットの順で進める。

## 6. 対象ファイル

`storage/settings.py`、`ui/settings_dialog.py`、新規`ui/video_panel.py`、`app/`と`ui/main_window.py`、対応試験、README/docs、本書。

## 7. 検証

製品試験・型・lint・buildはnot run。代表画面は実装後に確認する。実機取得はユーザーの準備完了連絡までnot run。実機の文字判読・比率・切断・終了は未完の受入条件として残す。

P1: `uv run --no-sync pytest tests/unit/test_video_settings.py tests/unit/test_settings.py tests/integration/test_settings_io.py --basetemp=tmp/pytest-video-settings -q`で設定の旧値・既定値・型/範囲・コメント・既存ファイル保護を検査。初回green34件、その後保存値が既存の整数と等しくなるbool/floatの拒否を追加した。対象ruff・tyも成功。各APIの属性説明と`docs/settings.md`へ範囲を記載した。

P1の最終結果は36件成功。P2: `uv run --no-sync pytest tests/integration/test_video_settings_gui.py tests/integration/test_app_settings.py --basetemp=tmp/pytest-video-gui -q --tb=short`で25件成功。機器一覧からのパス選択・同名の区別・未保存入力維持・手動編集との同期・入力制限と既存設定画面の回帰を確認。実機列挙ボタンの接続はP4で行う。対象ruff・ty成功。

## 8. 先送り事項

観測保存・MCP・新フレーム待ちはU04、入力はU05。ユーザー準備待ちの実機検証は本工程内の未完事項であり、後続へ移して完了扱いにしない。

## 9. 完了チェックリスト

- [x] 機器非依存の設定とM1依存の結合を分けた
- [ ] TDD項目を検証しコミットした
- [ ] 製品gateと文書・型・docstringを確認した
- [ ] 代表画面と必要な実機受入を実施し未確認範囲を区別した
