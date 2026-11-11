# U03 M0 映像取得方式・設定・寿命の設計

## 1. 目的・状態・起点

状態: **complete（調査・設計完了）**。U03の最初の子仕様として、取得API・採用版・機器識別・解像度とFPS・フレーム共有・切断と終了の契約を決定した。M0完了時点では製品実装と実ゲームのプレビューは未検証であり、本書だけの完了をU03全体の完了とは扱わない。後続の実装・実機受入結果は[U03親仕様](../unit_008/VIDEO_CAPTURE.md)へ集約した。

起点は2026-11-11のユーザー依頼と[U03親仕様](../unit_008/VIDEO_CAPTURE.md)。NyXの[映像I/O](https://github.com/niart120/Project_NyX/tree/master/src/nyxpy/framework/core/io)を参照し、そのまま採用できる構造と変更する振る舞いを分ける。

利用場面は、利用者が一つのキャプチャーデバイスを選び、実映像を確認し、明示的に切断またはアプリを終了すること。

## 2. 対象範囲・対象外

API・依存の一次資料、現環境の機器一覧、取得方式の成立確認、共有と終了の設計を対象とする。製品実装とそのTDDは次の子仕様に置く。保存・MCP・入力・ゲーム攻略は対象外。

## 3. 関連文書・前提

[実行管理](../../initial/02-runtime.md)、[ゲームI/O](../../initial/03-game-io.md)、[工程](../../initial/05-implementation.md)、[検証](../../initial/07-verification.md)、[U01](../unit_001/APP_STARTUP.md)を参照する。設定とGUIは実装済み。U01の取得機器の選択文字列には、識別・接続の保証がない。

## 4. 調査結果と採用判断

### 4.1 NyXから引き継ぐ構造と変更点

GitHub pluginで確認したNyXの`master`は`648f860e5afd582f80aa58645570a16bfc68a393`で、参照したローカルHEADも同じだった。以下は固定コミットの静的読取であり、NyXの実機動作を再検証した記録ではない。

| 対象 | NyXの実装 | Gigasでの判断 |
|---|---|---|
| 取得元の共有 | [device_factories.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/io/device_factories.py)が共有取得元を生成し、[adapters.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/io/adapters.py)から最新画像をコピーして読む | 一つの機器所有者と最新画像の共有を採用。Qtや後続MCPから別々に機器を開かない |
| 列挙・接続 | [device_discovery.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/hardware/device_discovery.py)はWindowsでDirectShowを列挙し、indexと`CAP_DSHOW`を取得元へ渡す | APIの明示は採用。保存値はindexや名前ではなくDevicePathとし、接続直前に再解決する |
| 解像度・FPS | [camera_capture.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/hardware/camera_capture.py)は60 FPS、1920×1080を要求し、寸法不一致時に720×480を要求する | 要求値を設定化し、自動の低解像度化を採用しない。要求値・API報告値・実画像寸法・実測頻度を区別する |
| フレーム・終了 | 同ファイルは最新1枚を保持するが、read失敗時に旧画像を無効化せず、終了は無期限のthread join後にreleaseする | 最新1枚は採用。停止状態、連番、時計を追加し、取得処理は期限付きで終了を管理できる子プロセスへ隔離する |
| 表示・対象外機能 | [preview_pane.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/gui/panes/preview_pane.py)はQtタイマーとBGR画像で表示し、snapshot・touchも持つ。factoryにはdummyへの代替がある | 表示頻度の分離とBGR表示を採用。固定16:9、保存、touch、dummyへの代替は持ち込まない |

### 4.2 採用する依存とAPI

Windowsの取得APIをOpenCV `CAP_DSHOW`へ限定する。列挙とopenに同じAPIを明示し、`CAP_ANY`による別APIへの切替は行わない。DirectShowはMicrosoftが新規利用でMedia Foundation等を推奨する既存APIであるが、今回はNyXとの対応を読めることと第8節の対象機器で取得が成立したことを採用理由とする。Media Foundationの実機互換性・性能は比較未実施であり、優劣を結論しない。

| 依存 | 採用版 | 確認と理由 |
|---|---|---|
| `opencv-python-headless` | `4.14.0.94` | NyX lockの`opencv-python`と同じ4.14系。PySide6で表示するためHighGUIを不要とする。Windows x64の`cp37-abi3` wheelを確認 |
| `cv2-enumerate-cameras` | `1.4.0` | index・name・path・backendを取得でき、型stubと`py.typed`を提供。Python >=3.10、Windows x64の`cp310-abi3` wheelを確認 |
| `numpy` | `2.5.4` | Python >=3.12、Windows x64のCPython 3.12〜3.15用wheelを確認。GigasのPython宣言範囲と整合 |

PyPIの[OpenCV配布説明](https://pypi.org/project/opencv-python/)、[headlessの採用版](https://pypi.org/project/opencv-python-headless/4.14.0.94/)、[列挙ライブラリ](https://pypi.org/project/cv2-enumerate-cameras/1.4.0/)、[NumPy](https://pypi.org/project/numpy/2.5.4/)と各版のJSON metadataを確認した。wheelの存在は全Python版での製品実行確認ではない。

NyX lockとvenv metadataはOpenCV `4.14.0.94`、列挙`1.3.3`、NumPy `2.3.5`だった。ただし`opencv-contrib-python 4.10.0.84`も共存しており、metadataだけから実import版を断定しない。OpenCV配布元は同じ`cv2`を提供するvariantの複数導入を避けるよう指定している。Gigasにはheadlessだけを導入し、NyXの依存一式を複製しない。

APIについて確認した制約は次のとおり。

- [OpenCVのset契約](https://docs.opencv.org/4.13.0/d8/dfe/classcv_1_1VideoCapture.html)では、戻り値trueでも機器が要求値を受け入れた保証はない。画像寸法と報告値を別途確認する。
- [4.14.0のvideoio.hpp](https://github.com/opencv/opencv/blob/4.14.0/modules/videoio/include/opencv2/videoio.hpp)で、open/read timeoutプロパティはFFmpeg・GStreamer用とされる。DirectShowの期限保証には使わない。
- [4.14.0のDirectShow実装](https://github.com/opencv/opencv/blob/4.14.0/modules/videoio/src/cap_dshow.cpp)は`CAP_PROP_BUFFERSIZE`の設定を処理せず、未対応値のsetはfalseとなる。内部の画像待機1000 msも、open・ドライバー・COM終了全体の期限保証ではない。NyXの`BUFFERSIZE=1`要求を低遅延保証として採用しない。

### 4.3 機器識別

`capture.device`には列挙結果の非空DevicePathを保存する。名前は表示用、indexは今回の列挙からOpenCVへ渡すための一時値である。[MicrosoftのDevicePath契約](https://learn.microsoft.com/en-us/windows/win32/directshow/selecting-a-capture-device)は同モデルの複数機器を区別する用途を支持している。ただしUSB端子変更やドライバー再導入を超える不変性は保証しない。

接続直前に再列挙し、保存文字列とpathの完全一致が一つだけある場合に接続する。不在・重複・空path、旧設定の名前やindexに対する推測の代替は行わない。pathが空の仮想機器は一覧で識別できても選択・保存の対象にはしない。同名機器は表示名に現在のindex等を添えて区別し、保存にはpathを使う。

## 5. 振る舞い仕様

### 5.1 モードと表示

要求の既定値は幅1920、高さ1080、取得60 FPS、表示30 FPSとする。FourCCはOpenCVと機器の自動選択に任せ、U03では設定を追加しない。必要性が実機で判明した場合に仕様へ戻って再検討する。第8節の調査で使ったMJPGを全機器の必須条件にしない。設定の型・範囲とGUIの入力方法はM1・M2の子仕様で具体化する。

取得画像はBGR、uint8、3チャンネルとし、実画像寸法が要求と異なる場合は接続失敗として扱う。FPSは要求・API報告・実測到着頻度を分け、報告値だけで60枚/秒の取得を保証しない。フレームを読み続ける処理へ表示用のsleepを入れず、GUIの更新頻度だけを表示FPSで制限する。表示は実画像の縦横比を保持し、拡縮・余白の付与で共有画像を変更しない。

### 5.2 所有・共有と時刻

列挙・open・read・releaseはspawnで起動する専用子プロセスが所有する。親の監視処理はGUIスレッド外で動かし、接続処理や待機でQtのイベント処理を止めない。具体的なクラス境界はM1で確定する。

子と親の間は固定長の生画像共有バッファ1枚と期限付きLockを使う。生画像サイズは要求モードから上限を決め、寸法不一致の画像をバッファへ書かない。親が保持する最新Frameは変更不能なBGR bytes、画像寸法、接続世代、取得連番、UTC取得時刻、単調増加時計の取得値を持つ。画像とmetadataを一つの整合した更新として公開する。GUI用や後続観測用の参照はこの同じFrameを使い、別の取得元を開かない。

連番と時計はread成功時にだけ更新する。連番は接続世代と組で比較し、再接続で前の接続の画像を参照しない。M1の具体化で、世代はサービス寿命内の単調増加整数とした。U03は永続化しないためUUIDを持たず、プロセス再起動や複数サービスをまたぐ保存IDはU04で別途付与する。UTCは記録表示、単調増加時計は期限と前後比較に使う。時刻は子がPC上で画像を取得した時点であり、機器内部の滞留やゲーム画面生成からの遅延を除去した時刻ではない。

共有バッファ、親の最新Frame、GUIの表示用画像をそれぞれ必要な一枚分に限定し、全フレームをGUI signalやキューへ積まない。GUIが遅れた場合は中間画像を捨て、最新画像へ追いつく。QImageへ渡すbytesの寿命を保つかコピーして、表示中のメモリを無効にしない。

### 5.3 接続失敗・切断・終了

| 境界 | 期限・期待結果 |
|---|---|
| 接続開始 | 列挙・open・初回フレームまで最大10秒。成立しなければ理由付きで失敗し、停止処理へ進む |
| 継続取得 | read失敗を通知する。readが戻らない場合も新しい取得が3秒途絶えたら取得停止とし、旧画像を有効な最新画像として返さない |
| 明示切断・アプリ終了 | 小さい停止通知を片方向Pipeで子へ渡し、正常終了を最大2秒待つ。子は取得ループを終えてreleaseする |
| 正常終了しない場合 | 所有する子プロセスをterminateし、終了確認のjoinを最大2秒行う。強制終了は正常なrelease完了と区別する |
| 終了未確認 | 子の終了を確認できなければその状態と所有参照を保持し、正常切断や再接続可能を報告しない |

状態・エラー・終了確認は画像到着と分けて通知する。停止開始後の遅れたフレームは公開せず、切断や失敗で親の最新FrameとGUI画像を無効化する。自動再接続やdummyへの代替は行わない。

[Pythonのプロセス終了契約](https://docs.python.org/3/library/multiprocessing.html)では、強制終了中のPipe・Queue破損や保持中のLockによる停止が起こり得る。親は共有Lockを無期限に待たず、終了した接続の共有バッファ・Lock・Pipeを次の接続へ再利用しない。監視処理、子プロセス、共有資源の寿命と終了未確認の試験はM1の責務とする。

## 6. TDD Test List

この子は調査・設計のため製品TDDはnot applicable。第5節をM1の取得・共有・終了とM2のGUI結合に分けて引き継ぎ、着手する子で振る舞い単位のTDD Test Listを作る。保存IDの再解決、所有画像の不変性、遅い表示、取得停止、接続失敗、停止不能な子の終了処理を観測可能な契約として検証する。文章やチェック欄の一致はテストにしない。

## 7. 対象ファイル

本書、U03親仕様、影響する初期設計と参照元。調査用の`tmp/u03/enumerate.py`、`tmp/u03/probe.py`、`tmp/u03/probe-3132.py`、`tmp/u03/probe-gvusb2.py`、確認画像は管理対象外とする。プローブの画像書き出しは調査資料であり、製品の画像保存機能を実装したものではない。依存定義・lock・公開APIはM1で変更する。

## 8. 検証

2026-11-11開始時: `main` / `8043c86`、作業ツリー変更なし。`codex/u03-video-preview`を作成し親をwipへ移動。

`Get-PnpDevice -PresentOnly`による読み取り専用調査で、USB ビデオ デバイス（VID 07CA / PID 0575）、Elite Go GC313Pro（VID 07CA / PID 3132）を確認。通常sandboxではCIMアクセス不可のため昇格して実施した。これは列挙の根拠であり、映像接続の成立確認ではない。

### 8.1 採用版での列挙と取得

調査用環境は`uv run --no-project --with opencv-python-headless==4.14.0.94 --with numpy==2.5.4 --with cv2-enumerate-cameras==1.4.0`で構成した。製品環境へ依存を導入した記録ではない。

| コマンド・確認 | 結果 | 根拠と限界 |
|---|---|---|
| 上記uv引数に続けて`python tmp/u03/enumerate.py` | pass | DirectShowでindex 0: Elite Go GC313Pro（VID 07CA / PID 3132）、index 1: USB Video Device（VID 07CA / PID 0575）、index 2: GV-USB2（VID 04BB / PID 0532）を列挙。仮想機器2件はpathが空 |
| 同じuv引数に続けて`python tmp/u03/probe.py`、通常sandbox | fail | PID 0575のopenが失敗。この実行だけで機器非対応と判断しなかった |
| 同プローブ、昇格して再実行 | 部分成立 | PID 0575でopen成功。MJPG、1920×1080、60 FPSを要求して180枚を取得しrelease。下表の通り、確認画像は黒でゲーム映像は未確認 |
| 同じuv引数に続けて`python tmp/u03/probe-3132.py` | 部分成立 | GC313Proでも同モードを要求し180枚取得。確認画像は全黒 |
| 同じuv引数に続けて`python tmp/u03/probe-gvusb2.py` | 部分成立 | GV-USB2の既定モードで60枚取得。確認画像は一色の青。view_imageでもゲーム映像がないことを確認 |
| 依存版・wheel・API・NyX読取 | pass | 第4節の一次資料と固定コミットを確認。静的調査と実測を分離 |
| PowerShellでMarkdownリンクを抽出し`Test-Path`、仮テキスト・行末空白を検査 | pass | 本書のローカルリンク6件が存在。仮テキストと行末空白なし。文章の意味は本書と参照元を読み合わせて確認 |
| 製品pytest・ruff・ty・lock・build | not applicable | M0は製品コード・依存定義・公開APIを変更しない。M1で変更範囲に対応するgateを実施する |

各プローブで確認した数値は次のとおり。所要時間はプローブ開始からの経過であり、ゲーム映像の遅延ではない。

| 測定 | USB Video Device / PID 0575 | GC313Pro / PID 3132 |
|---|---|---|
| open所要時間 | 約0.375秒 | 約0.312秒 |
| 設定要求 | MJPG、1920×1080、60 FPS。各setはtrue | 同左 |
| API報告値 | 1920×1080、60.00024 FPS | 同左 |
| 確認した画像 | shape `(1080, 1920, 3)`、dtype `uint8`。31枚目の確認画像は平均値0の全黒画像 | 同じ画像寸法・型、確認画像は平均値0の全黒画像 |
| 到着頻度 | 180枚の到着時刻から約47.54 FPS | 180枚の到着時刻から約59.9866 FPS |
| releaseを含む全体の経過 | 約6.328秒 | 約4.109秒 |

全体の経過はrelease単独の所要時間ではない。GV-USB2は既定の640×480で、API報告FPSは0、60枚のreadが成功した。確認画像の平均値は約85.6667で、一色の青だった。API報告FPSが0であることを、実取得頻度が0であることと混同しない。

この結果でDirectShowからBGR画像を取得する経路は成立した。全黒・一色の青の原因は未確定であり、入力信号、HDCP、出力機器の状態などを確認済みと扱わない。プローブでのrelease成功は、製品の監視・強制終了・資源管理の試験を置き換えない。

### 8.2 実装後に必要な確認

ユーザーから、接続済み機器のうち映像が出るものを選んでよいと回答を受けた。M0完了時点ではいずれの確認画像にもゲーム映像がなく、機器へ画面を出力できるか確認中だった。ゲーム文字の判読、縦横比、表示遅延、表示中の応答、製品からの明示切断・再接続・終了、物理抜去、取得不能時の強制終了はこの調査ではnot run。M1・M2で模擬境界の故障注入と実機試験を分け、成立した条件のみ親仕様へ報告する。対象ゲームの実映像が未確認のまま、U03親仕様を完了しない。

後続の最終受入ではユーザーの準備後、Live Gamer 4K 2.1系で製品経路の実映像・切断・再接続・終了を確認した。最新の成立条件と未検証事項は[M2実機記録](../unit_022/HARDWARE_ACCEPTANCE.md)を参照する。本節のプローブ値を製品経路の測定値へ読み替えない。

## 9. 先送り事項

以下はM0完了時点で後続へ渡した事項である。M1・M2による実装・実機確認の最新結果は親仕様と第8節末尾の受入記録を参照する。

| 事項 | 担当・期限 | 判断への影響 |
|---|---|---|
| 実ゲーム出力、黒・青画像の原因 | U03 M1の実機試験からM2受入まで。M0時点ではユーザー確認待ち | 基盤のTDDを先行し、実ゲームの判読と親工程の完了を保留した。後続でLive Gamerの実映像を確認したが、調査時の黒・青画像の原因そのものは特定していない |
| FourCC自動時の成立条件、機器ごとの取得頻度 | U03 M1・M2 | MJPGでの2機器の成功を全機器・全モードへ一般化しない。必要条件は設定と利用手順に記す |
| 子プロセス監視・IPC・設定の具体的APIと故障注入 | U03 M1の実装前 | 第5節を詳細契約とTDDへ具体化。親工程の未実装機能を成功扱いにしない |
| 要求後の新フレーム待ち・画像確定・保存・MCP | U04 | Frameのサービス内接続世代・連番・時計・停止状態を引き継ぐ。永続IDは別途付与する。U03は観測APIを公開しない |
| 操作後の映像鮮度・ゲーム入力・即時停止 | U05 | ホスト取得時刻と機器内部遅延を区別し、入力との結合で実機確認する |

## 10. 完了チェックリスト

- [x] 対象範囲と対象外、実装前に必要な調査を分けた
- [x] 採用版・API・識別・モード・共有・終了の契約を確定した
- [x] NyXからの採用点と変更点を根拠付きで記録した
- [x] 実機で成立した条件と未確認条件を分けた
- [x] 次の実装子仕様へ渡せる状態にした
