# 映像取得API

取得サービスの入口は`gigas.game_io.capture`です。`CaptureService`、`CaptureReader`、`CaptureSnapshot`、`CaptureTimeouts`を公開しています。接続設定は`gigas.game_io.capture.config`、画像・機器の型は`gigas.game_io.capture.models`、最新画像の保持は`gigas.game_io.capture.frames`に置いています。

## 取得設定と画像

`CaptureOptions(device_path, width=1920, height=1080, fps=60)`は接続に使う機器識別子と要求値を保持する変更不能な値です。`width`・`height`は`int | None`で、整数の場合は幅16〜3840、高さ16〜2160、`fps`は1〜120の整数です。`width=None, height=None`なら機器へ幅・高さを指定しません。一方だけの`None`や真偽値、不正な範囲、空の`device_path`は`CaptureError`になります。

カメラの固定寸法は機器への要求です。実際の到着寸法と違っても、それを理由に画像を拒否したり指定寸法へ加工したりしません。受け入れる到着画像は幅16〜3840、高さ16〜2160の3チャンネルBGR・uint8です。各辺の上限超過、不正形式、取得開始後の寸法変化は失敗にします。要求値は呼出側の`CaptureOptions`、実寸法は`CaptureSnapshot.frame.width`・`height`から比較できます。

`DeviceInfo(name, path)`は表示名と機器識別子を扱います。`path`にはWindowsの機器パス、Linuxの`by-id`・`by-path`の別名、macOSの`uniqueID`を使います。恒久的な物理シリアル番号やOS間で共通の識別子ではありません。接続時は空でない識別子の完全一致で機器を解決します。表示名や列挙順の番号へ読み替えません。

`Frame`はBGR uint8・3チャンネルの連続した`bytes`、幅・高さ、接続世代、世代内連番、UTC取得時刻、`monotonic_ns`を持つ変更不能な値です。画素データのバイト数は`width * height * 3`と一致します。`captured_at`と`monotonic_ns`はホストで取得が完了した時点を示します。単調時計はナノ秒で、`0 <= monotonic_ns`が必要です。機器内部の遅延や撮像時刻を保証しません。プレビュー用の拡縮・黒帯はこの画像へ書き込みません。

接続世代は、同じサービスの寿命内で接続要求を受け付けるごとに増加します。一つの接続要求に含まれる代替方式の試行は同じ世代です。複数サービスやプロセス再起動をまたぐ永続的な観測IDではありません。[記録付き観測API](../data/recorded-observation.md)は独立した観測IDを発行します。返却する`Observation.source_frame`には元Frameの世代・連番・寸法を保持し、JSONLには元寸法だけを保存します。

## サービスの操作と状態

`CaptureService`はUIに依存しない監視スレッドです。生成時にはスレッドや機器を開始せず、`start()`を一度呼んでから要求を渡します。映像取得を開始するのは`connect(options)`を明示的に呼んだときだけです。Linuxの列挙ではデバイスを開いて機能を照会しますが、この照会は撮像の開始ではありません。

`refresh_devices()`は別の子プロセスで列挙し、`snapshot().devices`と`devices_revision`を更新します。初期revisionは0で、成功した列挙ごとに1増えます。空の列挙成功と未列挙を区別できます。`connect()`でも接続直前の列挙結果を同じ一覧へ反映するため、表示名の取得だけを目的とする追加列挙・機器openは不要です。代替候補を試す場合は再列挙するので、一つの接続要求中にrevisionが複数回増えることがあります。

接続・列挙要求は受付時に`True`を返します。映像取得中も一覧検索を1件受け付けます。接続・切断処理中、検索の重複、終了中、または前の取得の`cleanup`が`unconfirmed`なら`False`です。受付や一覧の更新は接続成功を意味しません。`phase == "streaming"`となり`frame`が得られた時点で、呼出側が実画像を参照できます。

`snapshot()`は変更不能な`CaptureSnapshot`を返します。

| 属性 | 型・意味 |
|---|---|
| `phase` | `idle`・`enumerating`・`connecting`・`streaming`・`stopping`・`error`・`closed`のいずれか |
| `devices` | `tuple[DeviceInfo, ...]`。最後に成功した列挙の機器一覧。取得中の機器が含まれない場合はその識別子を補う |
| `catalog_warning` | 機器一覧の部分失敗、または取得と並行した検索の失敗理由。取得の `error` と独立 |
| `catalog_searching` | 取得と並行する検索の実行中。取得状態は `streaming` を維持 |
| `catalog_cleanup_unconfirmed` | 並行検索の資源解放が未確認。以降の一覧検索とアプリ終了の完了を保留 |
| `devices_revision` | `int`。成功した列挙の更新回数 |
| `frame` | `Frame \| None`。最新の有効な画像。停止要求後・失敗後は`None` |
| `generation` | `int`。このサービス内の接続世代 |
| `error` | `str`。報告する失敗理由。失敗がなければ空文字列 |
| `cleanup` | `none`・`graceful`・`forced`・`unconfirmed`。解放の確認状態 |
| `backend` | `int \| None`。現在または最後に選択したOpenCVのAPI番号。選択前は`None`。0は注入した試験用実装に限る |
| `diagnostics` | `tuple[str, ...]`。試行した取得方式の失敗理由を順に保持する |

最新1枚を読むため、ホストが最初に参照する連番は1とは限りません。取得要求のFPSは実際の到着頻度を保証しません。

`diagnostics`は代替方式で接続できた場合も先行試行の失敗を残します。試行した方式の失敗理由に、共通開始期限の理由を含む場合があります。中間失敗は`error`へ出さず、代替試行を続けられない場合に最終失敗を確定します。`backend`と`diagnostics`は次の要求が受け付けられた時点で初期化されます。要求寸法と実寸法は前節の設定・Frameから参照し、この失敗履歴とは分けて扱います。

## 取得方式の選択と代替

OpenCV-headless、NumPy、cv2-enumerate-camerasを使います。採用バージョンは[パッケージ定義](../../../pyproject.toml)を参照してください。FourCCは機器の既定を使い、FPSプロパティは要求値として設定します。

| OS | 候補の優先順 |
|---|---|
| Windows | DirectShow → MSMF |
| Linux | V4L2 → GStreamer |
| macOS | AVFoundation |

OpenCVで利用可能な方式だけを列挙します。同じ機器識別子の候補を方式ごとに解決し、一つの方式内で識別子が重複する場合は接続を拒否します。Linuxでは現在のデバイスノードを安定した別名へ解決できるものを扱います。汎用の番号探索、表示名・VID/PIDによる同一機器の推測、既存Windowsパスの別OSへの変換は行いません。

一つの接続要求で試す方式は最大2つです。最初の有効画像を取得する前に失敗し、機器の`release()`正常復帰、子プロセスの正常終了、親の資源回収を確認できた場合だけ、未試行の候補へ進みます。次の試行は新しい子プロセスで同じ機器識別子を再列挙し、その方式の現在の番号で開きます。古い番号を別方式に使い回しません。

強制終了、終了未確認、停止・アプリ終了要求、最初の有効画像取得後の失敗では代替試行を行いません。全候補に共通の開始期限を適用し、期限後の最初の画像も接続成功にしません。取得開始後の自動再接続やダミー画像への切替はありません。

OS別の方式選択はカメラ取得層の契約です。アプリの既定設定パスはWindowsの`LOCALAPPDATA`に依存します。Linux・macOSのアプリ起動、権限、Linuxの安定識別子、macOSの`uniqueID`とOpenCV番号の実機対応は未検証です。4K原信号や全機器の対応モードも確認済みとはしません。

## New Nintendo 3DS XL

Windows の固定取得元は設定の `capture.device` と `CaptureOptions.device_path` に `ponkan:n3dsxl` を使います。一覧更新と通常のカメラ列挙時に子プロセスが USB 記述子を読み、対応候補が1台ならカメラ一覧にこの取得元を加えます。明示的な一覧更新でカメラの列挙が失敗した場合も、3DS 基板の検出結果を返します。0台は正常な列挙結果です。利用中の 3DS は別プロセスから識別できない場合があるため、取得中はその接続を根拠に候補へ残します。複数台やドライバーエラーは `CaptureSnapshot.catalog_warning` で通知し、列挙済みのカメラは選択できます。ponkan の接続時に改めて `list_capture_devices(profile="n3dsxl", backend="auto")` の対応候補が1台なら `open_capture()` を呼びます。個体の識別子を指定する API がないため、列挙と open の間は接続構成を固定します。ドライバー利用不可、0台、複数台、接続失敗は映像診断で確認できます。

カメラ用の要求寸法と取得FPSは、この取得元では使いません。Windows の標準方式で reader から上下縦結合・BGR の画像を受け取り、幅400×高さ480の Frame として公開します。単発の読み取り期限切れでは次の画像を待ち、画像の途絶は既存のサービス期限で扱います。終了時は子プロセスが reader を閉じます。OpenCV の API 番号を持たないため `snapshot().backend` は `None` です。

## 独立した読み手

`CaptureService.reader()`は読み取り専用の`CaptureReader`を返します。公開操作は`snapshot()`で、状態を含む`CaptureSnapshot`を取得します。開始・切断・終了は所有側の`CaptureService`が操作します。readerの生成・破棄では機器を開閉せず、複数のreaderが同じ取得元の最新画像を参照します。

プレビューと観測処理にはそれぞれ別のreaderを渡します。`snapshot()`が返った時点で取得側のロックは解放されています。読み手が古い`Frame`を保持していても、取得と他の読み手は新しい画像へ進み、保持した画素は変わりません。プレビュー変換は取得ロックの外で行い、観測側の変換・保存・送信はGUIスレッドの外で行ってください。

切断後の`snapshot().frame`は`None`ですが、すでに保持している値は書き換わりません。記録付き観測は`streaming`かつFrameがあるsnapshotを一度採用し、その後の取得停止でも採用画像を保存できます。要求後の新フレーム待ちや鮮度の再検査は行いません。画像保存は[記録付き観測API](../data/recorded-observation.md)が担当し、MCP接続はこの取得APIに含みません。

`FrameStore`はホスト側で最新1枚だけを保持します。`begin(generation)`で旧画像を消し、その世代の新しい連番だけを`publish(frame)`で受け付けます。受付結果は`bool`です。`latest()`は`Frame | None`を返し、`invalidate()`後は次の`begin()`まで画像を返しません。

## 停止と資源の解放

`disconnect()`は直ちに画像を失効させて解放を要求します。`shutdown()`は新しい要求を拒否し、同じ解放処理後に監視スレッドも終了します。どちらも呼出元で機器I/Oを待ちません。

GUIは終了要求後に`is_alive()`が`False`であり、かつ`snapshot().cleanup`が`unconfirmed`でないことを確認してから`join(0)`を呼びます。スレッドの停止だけでは資源の解放完了を意味しません。取得プロセス・共有領域・制御Pipeは試行ごとに新しく作り、再接続や代替試行に再利用しません。

`CaptureTimeouts(startup=10.0, frame=3.0, stop=2.0, terminate=2.0)`の単位は秒です。各値には正の有限値が必要で、不正値は`CaptureError`になります。

| 属性 | 待つ対象 |
|---|---|
| `startup` | 列挙結果、または接続要求全体の最初の画像。代替方式ごとに延長しない |
| `frame` | 取得開始後の次の新フレーム。サービスの既定はカメラで3秒、ponkanで5秒。`CaptureTimeouts`を明示した場合は両方に指定値を使う |
| `stop` | 停止要求後の通常解放。列挙結果受領後の子プロセス終了にも適用 |
| `terminate` | 強制終了を要求した子プロセスの終了確認 |

開始・フレームの期限切れ後も、解放と終了確認の処理は必要です。これらの期限を機器モードやFPSの設定として扱わないでください。

| `cleanup` | 確認したこと |
|---|---|
| `none` | ネイティブreleaseの成功通知なし。worker生成前の取消でもこの値になる。`phase`が`idle`なら資源未作成または回収済みであり、次の明示要求を受け付けられる。異常終了ではプロセスの死亡と親の資源解放を確認してから次要求を許可する |
| `graceful` | releaseの正常復帰、または列挙の完了を確認し、子と親の資源を回収した |
| `forced` | 通常停止の期限後に子を強制終了し、死亡と親の資源解放を確認した。releaseの正常復帰とは区別し、同じ要求の代替試行には進まない |
| `unconfirmed` | 子の終了または親の資源解放を確認できない。サービス自身が再接続と再列挙を拒否し、所有参照を保持する |

OSが強制終了を完了できない場合は`stopping`・`unconfirmed`を返し、監視を続けます。アプリ側で終了を確認できない資源を、解放済みと扱わないでください。設定変更や保存だけでは接続を自動復旧しません。

制御通知は最大64 KiBのJSONで、列挙は最大64機器までです。超過や不正な通知は失敗として扱います。画像はPipeへ流さず、最大3840×2160×3＝24,883,200 bytesの画素領域を持つ共有メモリを使います。実寸法分の画素と幅・高さ・時刻・連番を同じ短い期限付きLockで読み書きします。ホストの状態参照にはプロセス間Lock待ちを持ち込みません。

## 取得境界の差し替え

`CaptureService(backend_factory=..., timeouts=...)`で機器境界を差し替えられます。factoryはspawn子プロセスへ渡せる呼び出し可能な値とし、子の中で`CaptureBackend`を生成します。実機を開かずにプロセス間転送や停止を検証する場合も、同じサービスを使用できます。

`gigas.game_io.capture.backend.IndexedDevice(info, index, backend=0)`は機器識別子と方式固有の一時的な番号を組み合わせます。`CaptureBackend.enumerate()`は`tuple[IndexedDevice, ...]`を返し、`open(device)`は選択した`IndexedDevice`全体を受け取って機器を開きます。番号だけを引数にする契約ではありません。実機では明示したOpenCV API番号を使い、既定の0は試験用実装に限ります。

`device_catalog()`は同じ非空識別子を一つの表示候補へまとめます。`select_candidates()`は同じ識別子の方式別候補を順に返し、欠落・同一方式内の重複を拒否します。`acquire_frames()`はこの同期境界を使い、実寸法のFrameを`publish`へ渡します。`open()`が返したハンドルを`finally`で解放し、`released`は正常復帰後だけ通知します。末尾の任意引数`candidates`は候補APIと選択APIの通知、`api_preference`は再列挙時に選ぶ明示APIです。指定APIの候補が消えた場合に、別方式へ黙って置き換えません。

接続設定の型は`gigas.game_io.capture.config.CaptureOptions`から参照します。同モジュールの`resolve_capture_options(device_path, resolution, fps)`は保存済みの解像度プリセットを要求寸法へ変換し、検証済みの接続設定を返します。未接続の判定と接続の適用は呼出し側が担当します。
