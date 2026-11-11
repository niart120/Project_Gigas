# 映像取得API

`gigas.game_io.models.CaptureOptions` は接続に使う機器パスと要求モードを保持します。幅は16〜3840、高さは16〜2160、取得FPSは1〜120の整数です。既定値は1920×1080・60 FPSです。値が不正なら`CaptureError`を送出します。

`DeviceInfo`は表示名とDirectShowの機器パスを持ちます。`select_device(devices, device_path)`は完全一致したパスが一つだけある場合に返し、不在・空文字列・重複は失敗します。表示名や列挙順の番号への読み替えは行いません。パスはWindowsのデバイスインターフェースの識別子であり、恒久的な物理シリアル番号ではありません。

`Frame`はBGR uint8・3チャンネルの連続した`bytes`、幅・高さ、接続世代、世代内連番、UTC取得時刻、`monotonic_ns`を持つ変更不能な値です。時刻はホストで取得が完了した時点を示し、キャプチャーデバイス内部の遅延や撮像時刻を保証しません。

`FrameStore`は最新1枚だけを保持します。`begin(generation)`で旧画像を消し、その世代の新しい連番だけを`publish(frame)`で受け付けます。`latest()`は`Frame | None`を返し、`invalidate()`後は次の`begin`まで画像を返しません。表示用の加工には別のバッファを使い、共有した`Frame.bgr`を変更しません。

取得にはWindows DirectShow、OpenCV-headless 4.14.0.94、NumPy 2.5.4、cv2-enumerate-cameras 1.4.0を使います。FourCCはデバイスの既定のままです。要求寸法と実フレーム寸法が一致しない場合や、BGR uint8・3チャンネルでない場合は接続を失敗させます。FPSプロパティは要求し、機器の報告値を実測レートと分けて扱います。名前やindexの自動フォールバック、模擬映像への切替は行いません。

接続世代は同じサービスの寿命内で新しい接続ごとに増加します。複数サービスやプロセス再起動をまたぐ永続的な観測IDではありません。永続化する利用側はサービスの識別と保存IDを別に付けてください。

`gigas.game_io.capture.CaptureService`はUIに依存しない監視スレッドです。生成後に`start()`を一度呼びます。機器を開くのは`connect(CaptureOptions(...))`を明示的に呼んだときだけです。`refresh_devices()`は別の子プロセスで列挙し、`snapshot().devices`と`devices_revision`を更新します。初期revisionは0、列挙成功ごとに1増えます。空の列挙成功と未列挙を区別できます。

接続・列挙要求は受付時に`True`、別の要求中や終了中なら`False`を返します。受付は接続成功ではありません。`snapshot().phase`が`streaming`となり、`frame`が得られた時点で実画像取得を確認できます。`reported_fps`は機器の報告値、`measured_fps`は連番と単調時計から求めた取得レートです。

`disconnect()`は直ちに画像を失効させて解放を要求します。`shutdown()`は新しい要求を拒否し、同じ解放処理後に監視スレッドも終了します。どちらも呼出元で機器I/Oを待ちません。GUIは`is_alive()`または`join(0)`で終了を確認できます。`snapshot()`は変更不能な値で、最新フレーム以外の画像を蓄積しません。取得プロセス・共有領域・制御Pipeは接続ごとに新しく作り、再接続に再利用しません。
