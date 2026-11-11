# 映像取得API

`gigas.game_io.models.CaptureOptions` は接続に使う機器パスと要求モードを保持します。幅は16〜3840、高さは16〜2160、取得FPSは1〜120の整数です。既定値は1920×1080・60 FPSです。値が不正なら`CaptureError`を送出します。

`DeviceInfo`は表示名とDirectShowの機器パスを持ちます。`select_device(devices, device_path)`は完全一致したパスが一つだけある場合に返し、不在・空文字列・重複は失敗します。表示名や列挙順の番号への読み替えは行いません。パスはWindowsのデバイスインターフェースの識別子であり、恒久的な物理シリアル番号ではありません。
