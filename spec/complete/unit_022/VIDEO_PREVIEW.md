# U03 M2 映像設定とプレビュー結合

## 1. 目的・状態・起点

状態: **complete（P1〜P4a・統合gate・実機受入完了、PR検証で判明したP4b/P4cの修正記録を追記）**。[U03](../unit_008/VIDEO_CAPTURE.md)と[M0](../unit_020/VIDEO_CAPTURE_DESIGN.md)に従い、選択・接続・最新映像の表示・切断・終了をGUIへ結合した。

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

P4では`app/video.py`の`VideoController`がGUIと取得サービスを結合する。Qtタイマーは最長34 ms間隔で状態を参照し、画像変換だけを接続時に確定した表示FPSで制限する。表示60 FPSでは監視間隔も17 msとする。表示間隔の計測には高分解能の`perf_counter()`を使い、Frameの取得時刻には影響させない。`VideoView`は同じ世代・連番を再変換せず、表示待ちのフレームキューを持たない。映像とCodexは左右のsplitterで同時表示する。機器パスは読取専用の一行欄、エラーは高さを制限したスクロール可能なPlainText欄で示す。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | P1 映像モードを検査し既定値・コメント付きTOMLへ保存する | new | unit/integration | red: 属性不存在・不正型を拒否しない14失敗。green: 下記検証。独立した4数値の変換のみで構造変更不要 |
| refactor-skipped | P2 機器を選んでモードとともに明示保存する | new | integration | red: モード消失・選択部品未実装。green: 25件。設定選択の同期を局所化し、大きい構造変更なし |
| refactor-skipped | P2a 未選択へ戻したときに接続対象を消去する | regression | integration | 独立レビューで選択表示と保存値の不一致を発見。red: path-aが残る、green: 空文字へ同期 |
| refactor-skipped | P3 最新画像をコピーし実画像の比率で描画する | new | integration | red: widget未実装。green: 描画画素・比率・再接続・消去。QImage変換と描画のみで構造変更不要 |
| refactor-skipped | P4 設定snapshotで接続し遅い表示・切断・終了を結合する | new | integration | red: shellのサービス注入・GUI結合が未実装で6件失敗。green: 新規8件と既存55件。最初から表示・監視・shell寿命を分離しており追加の構造変更なし |
| refactor-skipped | P4a Windowsの粗い時計で表示更新が隔回になる退行を防ぐ | regression | integration | red: 30/60 FPSとも60枚中32枚だけ表示、2回再現。green: 各60枚を欠落なく表示し同時刻の重複イベントは抑制。表示時計の1行変更で構造変更不要 |
| refactor-skipped | P4b 設定ワーカーをGUIスレッドで破棄してから終了を通知する | regression | integration | red: destroyed通知がI/Oスレッドで実行された。green: 保存完了→GUIで破棄→stoppedの順とGUI応答を確認。破棄手順をretireへ集約し、追加の構造変更なし |
| refactor-skipped | P4c I/Oスレッド起動自体の失敗でもワーカーを回収する | regression | integration | red: QThread.start例外注入で15秒終了せず。green: 終了コード1と診断を返し、通常寿命も維持。起動と所属移動の順序だけを変更 |

各itemをred→green→構造整理判断→検証→コミットの順で進める。

## 6. 対象ファイル

`storage/settings.py`、`ui/settings_dialog.py`、新規`ui/video_panel.py`、`app/`と`ui/main_window.py`、対応試験、README/docs、本書。

## 7. 検証

下記の各TDD対象試験に加え、P4aまでの実装で全体の品質gate・配布検証・実機受入を実施した。`uv run pytest tests/unit tests/integration`で227件成功し、ruff format/check・ty・lock・build・隔離wheelの起動/終了も成功。PRでの再検証で発見した停止とP4bの修正検証は後段に記録する。[親仕様の統合gate](../unit_008/VIDEO_CAPTURE.md)に実行範囲を集約する。[実機受入記録](HARDWARE_ACCEPTANCE.md)はLive Gamer 4K 2.1系の文字判読・比率・切断・再接続・表示中終了と未検証事項を所有する。模擬映像の描画成功とは別の根拠として扱う。

P1: `uv run --no-sync pytest tests/unit/test_video_settings.py tests/unit/test_settings.py tests/integration/test_settings_io.py --basetemp=tmp/pytest-video-settings -q`で設定の旧値・既定値・型/範囲・コメント・既存ファイル保護を検査。初回green34件、その後保存値が既存の整数と等しくなるbool/floatの拒否を追加した。対象ruff・tyも成功。各APIの属性説明と`docs/settings.md`へ範囲を記載した。

P1の最終結果は36件成功。P2: `uv run --no-sync pytest tests/integration/test_video_settings_gui.py tests/integration/test_app_settings.py --basetemp=tmp/pytest-video-gui -q --tb=short`で25件成功。機器一覧からのパス選択・同名の区別・未保存入力維持・手動編集との同期・入力制限と既存設定画面の回帰を確認。実機列挙ボタンの接続はP4で行う。対象ruff・ty成功。

P3: `uv run --no-sync pytest tests/integration/test_video_preview.py --basetemp=tmp/pytest-video-preview -q --tb=short`成功。実際の描画画像の画素を読み、4:2画像の余白・リサイズ・最新画像への切替・切断消去・新世代の低い連番の表示を確認した。元Frameのbytesは変化しない。対象ruff・ty成功。

P4: `uv run --no-sync pytest tests/integration/test_video_gui.py --basetemp=tmp/pytest-video-shell -q --tb=short`で8件成功。明示列挙と未保存入力の保持、保存・再読込後も接続時の設定を維持すること、表示1 FPSで最新画像だけへ進むこと、読込失敗・保存中の切断、終了未確認で画面を閉じないこと、待機中のGUI応答、長い機器パス・PlainTextエラーでも800×800画面内に操作部品が収まること、サービス異常終了時の画像失効を確認した。1件は実際のspawnサービスと模擬機器peerを使い、画像表示・切断・再接続・アプリ終了・release記録を照合した。物理機器は開いていない。

P4a: 実機計測で表示30 FPSに対し約15.6 FPSの到着が観測されたため、表示の間隔判定を調査した。WindowsのPython 3.12.10では`time.get_clock_info()`が`monotonic`を`GetTickCount64()`・分解能0.015625秒、`perf_counter`を`QueryPerformanceCounter()`・分解能0.0000001秒と報告した。34 msごとのイベントでも粗い時計では31.25 msの差となり、次回表示期限33.33 msに届かず省略する。30/60 FPSに対する34/17 msのQtイベントを固定時計で60回再生する試験を追加し、変更前は両方で28枚の欠落を2回再現した。表示判定だけを`perf_counter()`へ変えた後、`uv run --no-sync pytest tests/integration/test_video_gui.py --basetemp=tmp/pytest-video-clock-green -q --tb=short`で10件成功。各イベントで最新画像を表示し、同時刻の重複イベントは抑制した。タイマーや描画処理の負荷によらず一定FPSを保証する試験ではない。実機の表示頻度改善はこの変更の試験時点では未検証で、統合検証で再測定する。

P4aの最終実機再計測では、表示へ渡した新しい画像の頻度は世代1が26.33 FPS、世代2が24.04 FPSだった。修正前の約15.6 FPSからの改善を確認し、上限30 FPSに対する実測値として記録した。一定の表示速度や機器内部の遅延を保証する結果ではない。測定方法と対象条件は[実機記録](HARDWARE_ACCEPTANCE.md)を参照する。

P4b: PR検証中に、ローカル全体試験とCIで終了試験が停止した。停止中のローカルプロセスから取得した2回のスタックでは、主スレッドが`test_video_gui.py`の応答確認からpytest-qtの`QTimer.timeout.connect`へ入りQt内部mutexを待ち、別のQtスレッドが`QObject::~QObject`からPySideの処理を経てGILを待っていた。対象試験は実機を開かず、各`waitUntil`は5秒の期限を持つ。通常の条件不成立や未保存確認ダイアログ待ちではこの停止を説明できない。

SettingsWorkerをI/Oスレッドへ移し、`QThread.finished → deleteLater`による破棄とGUI側のシグナル接続を競合させる診断用の最小再現でも、600回以降に同じ接続位置で停止した。`finished → deleteLater`自体は[Qtの公式パターン](https://doc.qt.io/qt-6/qobject.html#deleteLater)であり、従来の使用をAPI契約違反とは扱わない。[disconnectNotifyの注意事項](https://doc.qt.io/qt-6/qobject.html#disconnectNotify)に内部mutexと別スレッドからの呼出しの制約があること、[PySideの過去の修正](https://github.com/pyside/pyside-setup/commit/0c64d1b2c6e5e0951675ad9b22294db4a10741c7)もQObject mutexとGILの取得順を対象にしたことは今回の観測と整合する。ただし今回のnativeアドレスを現版の全ソース行へ照合したわけではなく、同一の上流不具合とは断定しない。

版を固定した資料では、[Qt 6.12.0のQObject破棄](https://github.com/qt/qtbase/blob/v6.12.0/src/corelib/kernel/qobject.cpp#L1105)が内部mutexを保持して通知し、[そのmutexの選択](https://github.com/qt/qtbase/blob/v6.12.0/src/corelib/kernel/qobject.cpp#L142)は131個の共有プールを使う。[PySide 6.12.0の動的スロット登録](https://github.com/pyside/pyside-setup/blob/v6.12.0/sources/pyside6/libpyside/dynamicslot.cpp#L388)にはGIL解放で囲まれていない追加接続がある。別のQObject同士でも競合し得るという推定を支えるが、今回の停止を特定の上流ソース行の不具合と認定する根拠にはしていない。

修正では設定ワーカーを自身のI/Oスレッドから生成元GUIスレッドへ戻し、そこで遅延破棄する。shellは`destroyed`のqueued通知と各スレッドの終了を確認してから`stopped`を送る。起動前の失敗でもGUIで回収できるよう、I/Oスレッドへの移動は`start()`時に行う。保存を中断せず、無期限join・GCの無効化・固定sleepを追加しない。

`uv run --no-sync pytest tests/integration/test_app_lifetime.py -k destroyed_on_gui --basetemp=tmp/pytest-worker-owner-red -q -o faulthandler_timeout=15 --tb=short`は変更前に破棄スレッドの不一致で失敗し、変更後に成功した。試験は遅い保存中のGUI応答、実ファイルの保存値、保存完了→GUIでの破棄→終了通知の順を観測する。同じ最小再現は修正後に2000回完走した。`test_app_lifetime.py`、`test_app_settings.py`、`test_app_entry.py`、`test_video_gui.py`、`test_codex_gui.py`、`test_codex_shutdown.py`の回帰68件と、対象の映像終了試験の100回再生が成功し、起動前後の失敗・保存待ち・終了取消し・映像とCodexの終了を確認した。対象ファイルのruff format/check・tyと`git diff --check`も成功。`retire()`の呼出しスレッドと最終I/O後という前提をdocstringへ記載し、type ignoreやcastは追加していない。この修正後の実機再確認と全体gateは親工程の再検証で扱う。

P4c: 独立レビューで`io_thread.start()`自体の例外を注入すると、ワーカーが未起動スレッドへ移動済みのためGUIから回収できず、終了通知まで進まないことが判明した。起動失敗の既存subprocess試験へこの境界を追加し、`uv run --no-sync pytest tests/integration/test_app_entry.py -k io_thread_start --basetemp=tmp/pytest-io-start-red -q --tb=short`で15秒の外側期限による失敗を確認した。`start()`内をI/Oスレッド起動→ワーカーの所属移動の順に変更し、起動失敗時はGUIに残るワーカーをP4bの手順で回収する。`uv run --no-sync pytest tests/integration/test_app_entry.py tests/integration/test_app_lifetime.py tests/integration/test_video_gui.py --basetemp=tmp/pytest-io-start-green -q -o faulthandler_timeout=15 --tb=short`で28件成功した。失敗時の終了コード1・RuntimeError診断・不要なTracebackやQThread破棄警告がないこと・設定ファイル非作成に加え、正常な起動、保存、ワーカー回収、映像の終了を検証した。

回帰は`uv run --no-sync pytest tests/integration/test_app_settings.py tests/integration/test_app_lifetime.py tests/integration/test_app_entry.py tests/integration/test_codex_gui.py tests/integration/test_video_preview.py tests/integration/test_video_settings_gui.py --basetemp=tmp/pytest-video-regression -q --tb=short`で55件成功。起動途中の失敗、設定保存の待機、終了取消し、既存Codex操作、設定選択・映像描画を検査した。

型とdocstringは`CapturePort`で監視サービスの境界を定め、状態・FrameはM1の型を再利用した。GUIからのjoinは終了確認後の`join(0)`だけで、無期限joinやtype ignore・castを追加していない。`uv run --no-sync ruff check`と`uv run --no-sync ty check --no-progress`を`src/gigas/app/video.py`、`app/shell.py`、`ui/main_window.py`、`ui/video_panel.py`、新規試験に対して実行し成功。Qt既存メソッドと衝突した初回の`disconnect`・`render`はそれぞれ`disconnect_capture`・`render_state`へ修正した。利用手順は`docs/video.md`へ記載し、GUIには説明本文を追加していない。

## 8. 先送り事項

観測保存・MCP・新フレーム待ちはU04、入力はU05。実機の取得・表示・切断・再接続・終了は本工程で確認した。物理抜去・機器内部の遅延、長時間・他モード・表示倍率の網羅確認は未検証として[実機記録](HARDWARE_ACCEPTANCE.md)に残す。

## 9. 完了チェックリスト

- [x] 機器非依存の設定とM1依存の結合を分けた
- [x] TDD項目を検証しコミットした
- [x] 製品gateと文書・型・docstringを確認した
- [x] 代表画面と必要な実機受入を実施し未確認範囲を区別した
