# U03 M2 映像設定とプレビュー結合

## 1. 目的・状態・起点

状態: **wip（GUI結合実装済み、統合gate・実機受入待ち）**。[U03](../unit_008/VIDEO_CAPTURE.md)と[M0](../../complete/unit_020/VIDEO_CAPTURE_DESIGN.md)に従い、選択・接続・最新映像の表示・切断・終了をGUIへ結合する。

利用者は設定画面で機器の一覧を更新し、機器とモードを選んで保存する。主画面で接続し、同じ取得元から最新映像を表示する。

## 2. 対象範囲・対象外

TOML設定の追加、機器一覧からの選択、縦横比を維持する表示、非同期の取得サービスとの結合、終了と代表画面確認が対象。画像保存・MCPはU04、ゲーム入力・即時停止はU05。模擬画像は試験のみに使う。

## 3. 前提・工程内の分割

[M1](../unit_021/VIDEO_CAPTURE_SERVICE.md)の取得基盤とGUIを結合する。モード設定の保存・編集はM0の確定契約にだけ依存するためM1と並行できる。取得サービスへの結合・受入はM1実装後に進む。2026-11-11のユーザー指示で実機取得を一時保留し、機器に依存しない実装・試験を進めた。その後、準備完了の連絡を受け、実機確認を統合検証として進める。

## 4. 振る舞い仕様

- `capture.width=1920`、`height=1080`、`fps=60`、`preview_fps=30`を省略時既定値とする。正整数のみ、width 16〜3840、height 16〜2160、fps 1〜120、preview_fps 1〜60。既存device文字列は破壊・自動解釈しない。
- 設定画面に候補一覧と更新操作を追加し、選択するとdevice欄へDevicePathを設定する。同名候補は表示で区別する。列挙の成功を接続済みとして扱わない。保存は既存の明示操作だけで行う。
- 主画面の接続は最後に読込・保存成功した設定をsnapshotとして使う。設定の編集・保存・再読込だけでは接続中の機器・モードを変えない。
- GUIタイマーは設定した表示頻度で最新Frameを読み、同世代・同連番を再変換しない。中間フレームをキューへ積まない。BGRからQImageのコピーを作り、paintで縦横比を保って余白中央へ表示する。共有Frameを変更しない。
- 状態・要求モード・実寸法・FPS報告値と実測値を短いラベルと値で表示する。接続開始・切断・取得失敗で画像を消去し、理由を表示する。説明文や説明用ツールチップは置かない。
- 接続開始と一覧更新は重複受付しない。設定読込失敗・保存中でも既存接続の切断を受付ける。アプリ終了では映像を停止し、GUIを応答可能に保ちながらワーカーと子の終了確認を待つ。終了未確認を正常終了にしない。

P4では`app/video.py`の`VideoController`がGUIと取得サービスを結合する。Qtタイマーは最長34 ms間隔で状態を参照し、画像変換だけを接続時に確定した表示FPSで制限する。表示60 FPSでは監視間隔も17 msとする。`VideoView`は同じ世代・連番を再変換せず、表示待ちのフレームキューを持たない。映像とCodexは左右のsplitterで同時表示する。機器パスは読取専用の一行欄、エラーは高さを制限したスクロール可能なPlainText欄で示す。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | P1 映像モードを検査し既定値・コメント付きTOMLへ保存する | new | unit/integration | red: 属性不存在・不正型を拒否しない14失敗。green: 下記検証。独立した4数値の変換のみで構造変更不要 |
| refactor-skipped | P2 機器を選んでモードとともに明示保存する | new | integration | red: モード消失・選択部品未実装。green: 25件。設定選択の同期を局所化し、大きい構造変更なし |
| refactor-skipped | P2a 未選択へ戻したときに接続対象を消去する | regression | integration | 独立レビューで選択表示と保存値の不一致を発見。red: path-aが残る、green: 空文字へ同期 |
| refactor-skipped | P3 最新画像をコピーし実画像の比率で描画する | new | integration | red: widget未実装。green: 描画画素・比率・再接続・消去。QImage変換と描画のみで構造変更不要 |
| refactor-skipped | P4 設定snapshotで接続し遅い表示・切断・終了を結合する | new | integration | red: shellのサービス注入・GUI結合が未実装で6件失敗。green: 新規8件と既存55件。最初から表示・監視・shell寿命を分離しており追加の構造変更なし |

各itemをred→green→構造整理判断→検証→コミットの順で進める。

## 6. 対象ファイル

`storage/settings.py`、`ui/settings_dialog.py`、新規`ui/video_panel.py`、`app/`と`ui/main_window.py`、対応試験、README/docs、本書。

## 7. 検証

下記の対象試験・型・lintは実行済み。全体の品質gateと配布検証、代表画面、実ゲームの文字判読・比率・切断・終了は本書の実装記録時点ではnot runで、統合検証の受入条件として残す。模擬映像の描画成功は実機受入の代替にしない。

P1: `uv run --no-sync pytest tests/unit/test_video_settings.py tests/unit/test_settings.py tests/integration/test_settings_io.py --basetemp=tmp/pytest-video-settings -q`で設定の旧値・既定値・型/範囲・コメント・既存ファイル保護を検査。初回green34件、その後保存値が既存の整数と等しくなるbool/floatの拒否を追加した。対象ruff・tyも成功。各APIの属性説明と`docs/settings.md`へ範囲を記載した。

P1の最終結果は36件成功。P2: `uv run --no-sync pytest tests/integration/test_video_settings_gui.py tests/integration/test_app_settings.py --basetemp=tmp/pytest-video-gui -q --tb=short`で25件成功。機器一覧からのパス選択・同名の区別・未保存入力維持・手動編集との同期・入力制限と既存設定画面の回帰を確認。実機列挙ボタンの接続はP4で行う。対象ruff・ty成功。

P3: `uv run --no-sync pytest tests/integration/test_video_preview.py --basetemp=tmp/pytest-video-preview -q --tb=short`成功。実際の描画画像の画素を読み、4:2画像の余白・リサイズ・最新画像への切替・切断消去・新世代の低い連番の表示を確認した。元Frameのbytesは変化しない。対象ruff・ty成功。

P4: `uv run --no-sync pytest tests/integration/test_video_gui.py --basetemp=tmp/pytest-video-shell -q --tb=short`で8件成功。明示列挙と未保存入力の保持、保存・再読込後も接続時の設定を維持すること、表示1 FPSで最新画像だけへ進むこと、読込失敗・保存中の切断、終了未確認で画面を閉じないこと、待機中のGUI応答、長い機器パス・PlainTextエラーでも800×800画面内に操作部品が収まること、サービス異常終了時の画像失効を確認した。1件は実際のspawnサービスと模擬機器peerを使い、画像表示・切断・再接続・アプリ終了・release記録を照合した。物理機器は開いていない。

回帰は`uv run --no-sync pytest tests/integration/test_app_settings.py tests/integration/test_app_lifetime.py tests/integration/test_app_entry.py tests/integration/test_codex_gui.py tests/integration/test_video_preview.py tests/integration/test_video_settings_gui.py --basetemp=tmp/pytest-video-regression -q --tb=short`で55件成功。起動途中の失敗、設定保存の待機、終了取消し、既存Codex操作、設定選択・映像描画を検査した。

型とdocstringは`CapturePort`で監視サービスの境界を定め、状態・FrameはM1の型を再利用した。GUIからのjoinは終了確認後の`join(0)`だけで、無期限joinやtype ignore・castを追加していない。`uv run --no-sync ruff check`と`uv run --no-sync ty check --no-progress`を`src/gigas/app/video.py`、`app/shell.py`、`ui/main_window.py`、`ui/video_panel.py`、新規試験に対して実行し成功。Qt既存メソッドと衝突した初回の`disconnect`・`render`はそれぞれ`disconnect_capture`・`render_state`へ修正した。利用手順は`docs/video.md`へ記載し、GUIには説明本文を追加していない。

## 8. 先送り事項

観測保存・MCP・新フレーム待ちはU04、入力はU05。実機検証は本工程内の未完事項であり、後続へ移して完了扱いにしない。

## 9. 完了チェックリスト

- [x] 機器非依存の設定とM1依存の結合を分けた
- [ ] TDD項目を検証しコミットした
- [ ] 製品gateと文書・型・docstringを確認した
- [ ] 代表画面と必要な実機受入を実施し未確認範囲を区別した
