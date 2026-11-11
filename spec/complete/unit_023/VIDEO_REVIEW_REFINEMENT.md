# U03 人間レビュー後の映像UIと共有境界

## 1. 目的・状態・起点

状態: **complete**。2026-11-11の人間レビューで、FPS・寸法の常時表示、取得パスを手入力する設定、取得サイズと表示サイズの混同、複数読み手の検証不足、game_ioの平置きを指摘された。[U03親仕様](../unit_008/VIDEO_CAPTURE.md)を再開し、NyXの操作設計を参照して改善した。R1〜R4、最終実機・描画確認と全体品質gateを完了した。

[M0](../unit_020/VIDEO_CAPTURE_DESIGN.md)の機器識別・子プロセス・Frame不変性、[M1](../unit_021/VIDEO_CAPTURE_SERVICE.md)の取得基盤、[M2](../unit_022/VIDEO_PREVIEW.md)のGUI・実機受入を引き継ぐ。M2受入は当時の画面とコードの結果であり、この子の追加要件を検証済みとは扱わない。

## 2. 対象範囲・対象外

対象は、映像機器の選択UI、設定画面の構成、4段階の表示プリセット、主画面の情報整理、複数読み手の非占有性、取得モジュールの配置と公開説明。観測保存・MCP接続・新フレーム確定はU04、ゲーム入力と即時停止はU05。今回の複数読み手試験をMCP実接続の成功とは扱わない。

## 3. NyXの照合と採否

参照はProject_NyX `648f860e5afd582f80aa58645570a16bfc68a393`。M0と同じ版の`gui/layout.py`、`gui/panes/preview_pane.py`、`gui/dialogs/settings/device_tab.py`、取得・共有I/Oを照合する。

| 項目 | 採用・変更 |
|---|---|
| 機器選択 | 非編集の機器一覧と明示更新を採用。表示名と保存DevicePathを分離し、パス入力欄を置かない。列挙番号を永続IDへ戻さない |
| 取得と表示 | アプリの取得寸法は1920×1080に固定し、表示プリセットで取得を再接続・再設定しない。サービスAPIの寸法検査は維持する |
| 表示の4段階 | NyXのwindow/previewの組を採用。HD=1280×720/768×432、FullHD=1920×1080/1280×720（既定）、WQHD=2560×1440/1600×900、4K=3840×2160/2560×1440。取得画像の比率は維持する |
| 表示の選択と保存 | NyX同様に表示メニューの排他4項目と設定の選択欄を用意する。メニューの明示選択も非同期設定保存へ渡し、保存成功後に反映する。未保存の設定編集中・保存中はメニュー変更を受け付けず、編集値の上書きを避ける |
| 小さい画面・DPI | 作業領域を超える場合はウィンドウとプレビューの希望寸法を同じ比率で縮める。操作部品の領域を確保し、実プレビューは残り領域内へさらに縮小できる。画面外へ操作部品を押し出す固定寸法は採用しない |
| 診断情報 | 主画面の要求寸法・実寸法・報告/実測FPSと長いDevicePath欄を除く。数値はサービスAPIと検証記録に残し、常設のステータスバー表示も追加しない |
| フレーム | デバイスを一度だけ開く共有取得を維持し、読み手ごとに画像加工と保持の寿命を分ける。模擬画像・寸法フォールバック・無期限joinは採用しない |

## 4. 振る舞い仕様

### 機器と設定

設定を「映像」「エージェント」「入力」のタブへ分ける。「映像」には機器の非編集一覧と更新、取得FPS・表示FPS、表示プリセットを配置する。取得幅・高さと機器パスの手入力欄は置かない。「エージェント」は既存の認証方式・モデル・エフォート、「入力」は既存のシリアル保存値を所有し、U05の機器接続を実装済みと見せない。単一値には一行入力を使い、未変更の既存設定値は破壊しない。保存・再読込・閉じるは全タブ共通の下部操作とする。保存先は読取専用の一行欄とし、長い値で画面を広げない。

ユーザーは一覧のデバイス表示名を選ぶ。DevicePathは選択された項目の内部データとして保存し、人にパスの入力・目視照合・確定を求めない。同名機器だけ短い識別情報を添え、長いパスは画面へ出さない。更新は未保存の選択を維持する。保存済みパスが一覧にない場合は「未検出」の選択として保持し、別機器や空文字へ無断置換しない。ユーザーが「未選択」を選んだときだけ空にする。取得中は別の列挙プロセスを起動せず、直近の一覧を使う。

取得FPSと表示FPSは引き続き独立し、表示頻度は上限。NyXの15/30/60を基本候補とし、既存の範囲内の保存値も消さずに表示する。表示サイズは接続中も変更でき、取得元・取得世代・連番は変えない。設定保存の成功後に表示設定を反映し、読込/保存失敗で現在の表示や切断操作を失わせない。TOMLの`gui.window_size_preset`は`hd` / `full_hd` / `wqhd` / `four_k`を保持し、不正キーは明示エラーにする。既定は`full_hd`。表示FPSの既定はGigasの検証済み上限30を維持する。取得幅・高さはアプリ設定から取り除き、CaptureOptionsの既定1920×1080を使用する。

### 主画面

映像欄は接続・切断、短い状態と機器表示名、プレビュー、必要なエラー理由を持つ。FPSやピクセル寸法を常設表示しない。説明本文は置かない。「表示」メニューの「ウィンドウサイズ」から4段階を選ぶ。設定画面も同じキーと値を使い、表示領域と会話領域が画面内に収まる。実画面を読んで配置・余白・操作部品の可視性を確認し、固定の自然言語を照合するテストで代用しない。

### プレビューと後続観測の共有

取得子→共有領域→監視スレッド→最新1枚のFrameStoreを一つの取得経路とする。サービスの所有・開始停止と読み取りを分け、`CaptureService.reader()`が読み取り専用の`CaptureReader`を返す。各readerは`snapshot()`だけを公開し、connect/disconnect/shutdownの所有操作を持たない。readerの生成・破棄で機器を開閉しない。プレビューと後続観測は別のreaderを持つ。

`snapshot()`は状態と変更不能なFrameの参照を含む`CaptureSnapshot`を返し、参照を渡した時点でロックを解放する。プレビュー変換は取得ロック外で、観測側の画像変換・保存・送信はGUIスレッド外で行う。観測側が古いFrameを保持していても、取得とプレビューは新しい連番へ進む。保持した画像の画素は変わらない。NyXの読み手別portと共有所有者を踏襲し、mutable配列のcopyの代わりにimmutable bytesを安全に共有する。

U04はこの共有APIの上に要求後の鮮度条件・停止状態の再確認・画像の確定と保存を設計する。MCP要求ごとに別デバイスを開いたり、プレビューから画像を消費したりしない。実MCP・エンコード負荷の確認はU04で実施する。

### モジュールの配置

`game_io/capture/`へ取得のservice、backend、models、frames、private process transportをまとめる。`game_io/capture/__init__.py`からCaptureReader / CaptureService / CaptureSnapshot / CaptureTimeoutsを公開し、アプリ側の入口を保つ。内部モデル参照は新しいパッケージへ統一する。未公開のPR内の整理なので旧平置きへの互換shimは追加しない。将来の入力は隣の機能パッケージで分けるが、空のinputディレクトリは作らない。機器APIとUIを同じパッケージへ混ぜない。

## 5. TDD Test Listと進行順

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | R1 読み取り専用portを分け、遅い読み手が画像を保持しても取得とプレビューが進む | new/regression | integration | reader APIの欠如でred→2件green。実spawnでopen1回・同じ世代の連番進行・保持画素不変・切断時失効を確認。MCP実接続の検証ではない |
| refactor-done | R2 取得をcaptureパッケージへまとめる | characterization | unit/integration/package | R1のgreen後に移動。公開import・取得/GUI結合を3.12/3.13で各83 passed、配布wheelのspawn/終了も成功 |
| refactor-skipped | R3 機器を一覧だけから選択し用途別の設定を明示保存する | regression | integration | red: 旧寸法の使用・タブ/候補不足・未検出Path消失5件とpreview専用reader未使用1件。green: 対象27件。選択と旧値保護を小さい関数へ分け、追加構造変更なし |
| refactor-skipped | R4 表示プリセットを切り替えても取得と共有画像を変えない | new | unit/integration | red: 保存キー・寸法計算・メニュー未実装。green: 設定/配置56件・I/O9件・GUI回帰79件。保存境界と純粋配置計算へ分離済みで追加整理不要 |

R1→R2を先に完了し、参照先の移動を固定してR3→R4へ進む。各論理変更を別コミットにする。R1の既存の非占有性については現在の設計で成立するかをまず確認し、人工的なredを作らない。新しいreader APIとR3/R4の振る舞い変更はred→greenを記録する。R2は構造変更として変更前後の同じ契約を検証する。

## 6. 対象ファイル

`src/gigas/game_io/`、`app/video.py`・`app/shell.py`、`ui/settings_dialog.py`・`ui/video_panel.py`・`ui/main_window.py`、設定と配置計算、対応unit/integration、配布検証、docsと初期設計。UIの見直しをMCP・入力機能の実装へ広げない。

## 7. 検証計画と現在地

NyXとの仕様照合、R1〜R4の実装・模擬境界試験、最終の実機と代表画面を確認した。全体gate・配布・公開説明の確認結果は本節末尾、実機と描画の条件・限界は[受入記録](HARDWARE_ACCEPTANCE.md)に記す。Python 3.12/3.13のCIをPRの最新HEADで通してからマージする。

### R1: 読み取り専用APIと複数読み手

2026-11-11、`CaptureReader`と`CaptureService.reader()`を追加。readerは所有側のsnapshot参照だけを保持し、取得経路やフレームのqueueを増やさない。旧画像を保持する観測スレッドをEventで停止し、その間に別のプレビュー用readerで同じ世代の連番が5以上進むことを実spawnで確認した。子のopen記録は1回で、保持画素は不変、切断後の両readerは画像なし、停止中の読み手を待たずにgraceful解放した。実GUI・MCP・保存負荷はこの試験の対象外。

- red: `uv run --no-sync pytest tests/integration/test_capture_service.py -k reader --basetemp=tmp/pytest-capture-r1-red -q`。未実装のCaptureReaderのimportで失敗した。
- green: 同じ対象を`--basetemp=tmp/pytest-capture-r1-green`で実行し2 passed。既存の非占有性に人工的なredは作らなかった。
- 回帰: `uv run --no-sync pytest tests/unit/test_capture_models.py tests/unit/test_capture_frames.py tests/unit/test_capture_backend.py tests/integration/test_capture_service.py tests/integration/test_capture_failures.py --basetemp=tmp/pytest-capture-r1-312 -q -o faulthandler_timeout=30`はPython 3.12.10で53 passed。
- 型/docstring: `uv run --no-sync ruff check src/gigas/game_io tests/integration/test_capture_service.py tests/integration/peers/capture_peer.py`、同範囲の`ruff format --check`、`uv run --no-sync ty check --no-progress`は成功。戻り値CaptureSnapshot、画像のNone、ロック解放と保持画像の寿命をdocstring・公開API文書で照合した。新しいAny/cast/ignoreはない。
- tidy判断: R1は公開APIの振る舞い変更、R2の配置整理は別コミットへ分離する。reader追加は小さい委譲のみで重複整理は不要のためrefactor-skipped。GUI側の専用readerへの接続はR3で行う。
- 独立レビュー: 製品APIに必須指摘なし。試験側Eventの8秒期限が取得待機と停止待機の合計より短いという指摘を採用し、試験の`finally`で必ず解放する待機へ修正した。対象2件を再実行して成功した。

### R2: 取得パッケージの配置整理

2026-11-11、R1のgreenを起点に`game_io/capture.py`を`game_io/capture/service.py`へ、backend・models・framesを同パッケージ内へ、`_capture_process.py`を`capture/_process.py`へ移動した。新しい`capture/__init__.py`で4つの公開型を再公開し、製品・試験・spawn peer・配布検証・公開API文書の参照先を統一した。旧平置きの互換shimは設けていない。取得と停止の処理・試験の期待値は変えていない。

- tidy判断: R1とは独立した構造変更。人工的なredを作らず、変更前の53件greenに対して、変更後は取得系と影響するGUI/設定を合わせて83件検査した。
- 3.12: `uv run --no-sync pytest tests/unit/test_capture_models.py tests/unit/test_capture_frames.py tests/unit/test_capture_backend.py tests/unit/test_video_settings.py tests/integration/test_capture_service.py tests/integration/test_capture_failures.py tests/integration/test_video_gui.py tests/integration/test_video_preview.py tests/integration/test_video_settings_gui.py --basetemp=tmp/pytest-capture-r2-312 -q -o faulthandler_timeout=30`はPython 3.12.10で83 passed。
- 3.13: `UV_PROJECT_ENVIRONMENT=tmp/u03/ci-python313`を指定し、同じ対象を`uv run --python 3.13 --no-sync pytest`・`--basetemp=tmp/pytest-capture-r2-313`で実行してPython 3.13.5で83 passed。
- 配布: `uv run --no-sync python scripts/check_package.py`はsdist/wheelを作り、repo外の新規環境でresource一致、実spawnによる列挙と不存在パス拒否、子の終了、Qt非依存、module/scriptのGUI起動・終了を確認した。最初のsandbox実行は一時領域の書込拒否でbuild前に停止し、通常一時領域での再実行は成功した。機器は開いていない。
- 型/docstring: 変更Python範囲のruff/formatと全体`ty check --no-progress`、`git diff --check`は成功。公開入口は既存サービス3型とR1のReader、値型は`capture.models`に統一した。型の緩和やignore追加はない。
- 参照: src/tests/scripts/docs/READMEの旧`game_io.models`・`frames`・`backend`・`_capture_process`参照は0件。過去の完了仕様は当時の配置を記録する履歴として維持し、恒常設計は親作業で更新する。
- 独立レビュー: staged 21ファイルを読み、追加必須指摘なし。処理差分は公開入口の再公開とimportのみ、spawn target・peer・故障注入先・公開説明の新配置が一致することを確認した。

### R3: 機器選択と用途別の設定

機器パスの入力欄を取り除き、機器表示名から選択する非編集comboへ統一した。DevicePathは項目の内部データとして保持し、未検出の値も専用項目に残す。同名の候補だけ番号を添え、更新や他項目の編集で現在の選択を変えない。「未選択」の明示選択だけが保存値を空にする。3タブ、一行editor、15/30/60と既存FPS値の選択へ変更し、未編集の改行・空白を含む既存値を保持した。

アプリ設定から取得幅・高さを除き、接続では`CaptureOptions`の1920×1080既定値と保存した取得FPSを使う。旧TOMLのwidth/heightは未知項目として保持するが読取・接続には使わない。サービスAPIの可変寸法試験は維持した。プレビューは専用の`CaptureReader`を1つ持ち、取得の所有コマンドとは別の口から状態・画像を読む。

- red: `uv run --no-sync pytest tests/unit/test_video_settings.py tests/integration/test_video_settings_gui.py --basetemp=tmp/pytest-r3-red -q --tb=short`で5件失敗。旧寸法の読込/保存、タブ未実装、未検出値の項目欠如、FPS候補未実装を確認。GUI結合の専用reader試験も未使用で1件失敗した。
- green: 上記2ファイルと`test_video_gui.py`を合わせて`--basetemp=tmp/pytest-r3-green`で27件成功。機器選択・更新・未保存値・旧値・固定取得寸法・保存後の接続設定維持・実spawn表示と再接続を検証した。
- 設定と起動の回帰: `test_settings.py`、`test_video_settings.py`、`test_settings_io.py`、`test_app_entry.py`、`test_video_preview.py`の40件が成功。旧寸法以外の型・範囲、コメント・未知項目保護、起動失敗回収と描画を確認した。
- 既存GUIの回帰: `uv run --no-sync pytest tests/integration/test_app_settings.py tests/integration/test_app_lifetime.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py --basetemp=tmp/pytest-r3-existing-gui -q -o faulthandler_timeout=30`で53件成功。QLineEdit・一覧選択への操作変更後も、保存と再起動・未編集のCRLF/NBSP保持・保存失敗・終了取消し・Codexの寿命を検証した。
- 型/docstring/docs: `CapturePort.reader()`と`VideoController.reader`で読取境界を明示し、Settingsの公開属性から取得寸法を除いた。型緩和やignoreを追加せず、対応するruff/format・tyを検査した。現在の選択操作と旧寸法の無効化を`docs/settings.md`・`docs/video.md`へ記載した。新画面を読む検証と実機結合はR4後に行う。
- 独立レビュー: 必須指摘なし。接続中の表示FPS反映はR4で扱うことを再確認した。3タブのQt offscreen画像を読み、表示名・一行入力・下部操作の配置を確認した。描画環境の日本語フォント不足は検証ハーネスだけでMeiryoを登録して補い、製品フォントは変更していない。R4のプリセット追加後に再確認する。

### R4: 4段階の表示と非同期保存

`ui/layout.py`にNyXと同じ4組の希望寸法を持つQt非依存の計算を置いた。作業領域へ合わせてウィンドウとプレビューを同倍率で縮め、Qtの実配置では操作部品の領域を確保した残りへプレビューをさらに縮める。800×800のoffscreen画面でも各プリセットのフレームと接続・送信・設定操作が画面内に収まることを確認した。

設定のcomboと「表示→ウィンドウサイズ」の排他メニューは同じ4キーを使う。メニューも既存の設定ワーカーによる非同期保存を通り、成功時だけサイズを反映する。未保存編集中・保存中・終了中・設定読込失敗時はメニューを無効にする。保存失敗では選択と表示を直前の保存値へ保ち、切断操作を妨げない。接続中の表示FPSも取得設定とは独立して反映し、保存成功・再読込成功のときだけ更新する。

主画面から要求/受信寸法・報告/実測FPSとDevicePath欄を除き、機器表示名・短い状態・接続/切断・必要なエラーだけを残した。取得APIの数値情報は削除していない。

- red: 設定の新キー試験14件と旧設定の既定値試験1件が属性欠如/不正値未拒否で失敗。寸法計算は新module不存在で失敗。`test_window_presets.py -k all_presets --basetemp=tmp/pytest-r4-gui-red`はメニュー未実装で失敗した。
- 設定・配置green: `test_settings.py`、`test_video_settings.py`、`test_window_layout.py`を`--basetemp=tmp/pytest-r4-settings-regression`で実行し56件成功。4キーの保存・省略時既定値・型と未知キー拒否・ファイル保護・4組の寸法・同率縮小・拡大しない計算を確認した。`test_settings_io.py`の9件も成功。
- GUI green: `uv run --no-sync pytest tests/integration/test_window_presets.py tests/integration/test_video_gui.py tests/integration/test_video_settings_gui.py tests/integration/test_video_preview.py tests/integration/test_app_settings.py tests/integration/test_app_lifetime.py tests/integration/test_app_entry.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py --basetemp=tmp/pytest-r4-regression -q -o faulthandler_timeout=15 --tb=short`で79件成功。保存待ち/失敗、編集値の保護、メニューと設定の同期、表示FPSの接続中反映、再読込、画面内の操作配置を検証した。
- 実spawn結合: 独立readerが保持する旧Frameを残したままGUIで表示サイズを保存・反映し、プレビューreaderの新連番・同世代・1920×1080・保持画素不変・機器open1回を照合した。終了時のreleaseも1回。物理機器や実MCPはこの試験では使っていない。
- 型/docstring/docs: 希望寸法計算とGUI適用を分け、設定の受理キーと保存成功後の反映を公開文書へ反映した。全体tyと対象ruff/formatを検査した。新規の型ignore/castはない。
- 独立レビュー反映: 事前の一覧更新なしで保存機器へ直接接続すると、取得は成立しても表示名が空欄になる指摘を採用した。GUI実spawn試験で5秒待っても名前が出ないredと、coreの通知/一覧/開始期限の3件redを確認。接続に使う一度の列挙結果をホストの`devices`と`devices_revision`へ伝え、主画面の表示名と取得中の設定候補を同じ情報から更新した。追加列挙や追加openはしない。列挙専用操作の完了フラグはその操作だけに限定し、取得側の開始期限・停止・解放確認を維持した。
- 修正後: 取得unit/integration55件と`uv run --no-sync pytest tests/integration/test_window_presets.py --basetemp=tmp/pytest-r4-name-green -q -o faulthandler_timeout=15 --tb=short`の5件が成功。保存Pathの直接接続、表示名、設定候補、同じ列挙1回、一覧通知後のopen固着期限を確認した。再度の独立読取レビューで追加必須指摘なし。
- 画面確認: 4096×2400と800×800のQt offscreen描画で、設定3タブと主画面4段階を読み、作業領域内の配置と操作の可視性を確認した。最終実機での直接接続・4段階切替・切断/再接続・終了は[実機受入記録](HARDWARE_ACCEPTANCE.md)で別に扱う。

### 最終統合確認

製品コード`2ffb799`で、`uv sync --dev`、`uv lock --check`、`uv run ruff format --check .`（129ファイル）、`uv run ruff check .`、`uv run ty check --no-progress`、`uv build`、`uv run python scripts/check_package.py`、`git diff --check`が成功した。全体の`uv run pytest tests/unit tests/integration -q -o faulthandler_timeout=30`はPython 3.12.10で268 passed / 22.13秒、別環境のPython 3.13.5でも268 passed / 28.69秒。後者は`UV_PROJECT_ENVIRONMENT=tmp/u03/ci-python313`と`uv run --python 3.13 --no-sync pytest`を使った。

配布検証はrepo外の新規環境へwheelを入れ、資材同梱、実spawn・不在機器の拒否・回収、module/console起動のGUI開閉を確認した。[実機受入](HARDWARE_ACCEPTANCE.md)は保存済み機器への直接接続、4段階切替、表示名選択・保存、切断・再接続・表示中の終了を確認している。R1〜R4と起動/終了修正の独立レビュー指摘は対応済み。文書と実測JSON、機能範囲、公開型・docstringを読み合わせ、設定の空文字の説明も対象項目に限定した。skill本体は変更していないためvalidatorはnot applicable。PRの最新HEADに対するWindows CI結果はPRで確認する。

## 8. 先送り事項

観測確定・保存・MCPはU04、入力の機器一覧と接続・即時停止はU05、モデル候補と接続中の実行設定変更はU06。機器内部/光学遅延・物理抜去・長時間連続運転は元の実機記録と同じく未検証として区別する。

## 9. 完了チェックリスト

- [x] 人間レビューとNyXの採否を設計へ反映した
- [x] R1〜R4の検証と論理コミットを完了した
- [x] 実画面を読み、必要な実機再確認と未検証事項を記録した
- [x] 品質gate・配布・docs・親仕様の受入を再確認した
