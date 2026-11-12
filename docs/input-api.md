# 入力状態と操作列API

`gigas.game_io.input.models.parse_sequence(value, protocol)` は、JSONの`sequence`配列全体を検査し、不変の`tuple[InputStep, ...]`へ変換します。機器への送信は行いません。プロトコルは`gigas.game_io.input.config.get_protocol("ch552")`、`"pokecon"`、`"3ds"`から選びます。

```python
from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState, parse_sequence
from gigas.game_io.input.protocol import encode_state

protocol = get_protocol("ch552")
steps = parse_sequence(
    [
        {"kind": "press", "buttons": ["A"], "duration_ms": 100},
        {"kind": "wait", "duration_ms": 250},
    ],
    protocol,
)
pressed = encode_state(steps[0].state, protocol)
neutral = encode_state(InputState(), protocol)
```

`press`は完全状態を保持してから解除する入力、`wait`は未押下での待機を表します。時間は正の整数ミリ秒です。配列の要素数、入力時間、合計時間の固定上限は設けません。無限生成器、期限なしhold、未知の項目、非対応ボタン、小数・文字列・boolの時間や座標は`InvalidSequenceError`で拒否します。

ボタンは重複なしの名前配列です。`UP/RIGHT/DOWN/LEFT`は隣接2方向まで同時指定でき、反対方向は拒否します。左右stickは`{"x": -100..100, "y": -100..100}`、右と下が正で、省略時は中心です。3DSのtouchは`{"x": 0..319, "y": 0..239}`、省略またはnullで解除を表し、映像の座標とは独立しています。入力のないpressは拒否します。

| プロトコル | ボタン | touch |
|---|---|---|
| CH552 / PokeCon | A/B/X/Y/L/R/ZL/ZR/HOME、PLUS/MINUS/LS/RS/CAPTURE、方向 | 非対応 |
| 3DS S2/T3 New Firmware | A/B/X/Y/L/R/ZL/ZR/HOME、START/SELECT、方向 | 対応 |

`InputState`は直接構築する場合も整数範囲・重複・反対方向を検査します。`encode_state`は対応入力を検査してから毎回完全なフレームを作り、以前の状態を保持しません。`InputStep.to_json()`は記録に使える独立したJSON値を返します。byte列への変換成功は実機の受信・操作・解除の確認を意味しません。

## シリアル接続API

`resolve_serial_config(port, protocol, baudrate)`は設定を接続用の値へ解決します。空portなら`None`、baudrateが`None`ならプロトコルの既定値になります。候補と検証は`PROTOCOLS`を共用し、対応しない値は`ValueError`になります。

`gigas.game_io.input.serial.list_serial_ports()`はpySerialが列挙する各OSのネイティブポートの識別子と説明を返します。機器は開きません。GUI・イベントループ外から呼んでください。OS別の列挙・送信はpySerialに委ねます。シリアル入力の実接続・実機動作は各OSとも未検証です。

`SerialInput(config)`は単一workerを所有します。`await open()`で現在の一覧からport識別子が一意に一致する機器を開き、neutralを送出します。`await send(state)`は完全状態を一度送信します。8N1、フロー制御なし、read timeout 0、write timeoutなし、DTR/RTS有効です。URL・ネットワークシリアルは対応しません。

送信の全byte数が返った場合だけ完了します。部分送出や例外は`SerialInputError`となり、自動再送・flush・ACK待ちは行いません。取消しは開始済みwriteが戻るまで回収し、その間の別の送信・closeは拒否します。保持時間の待機・停止後のneutralは操作実行側が担当します。

接続所有者はI/O終了後に`await close()`を呼びます。close自体は追加のneutralを送信しません。open失敗後もworkerを解放するためcloseが必要です。実ドライバーの停止時間やゲーム側の解除は、これらのAPIの成功だけでは保証しません。

## 記録付き実行API

`gigas.services.input.InputService(reader, observations)`は、一つの入力接続・操作権・実行中の列を所有します。既存の`CaptureReader`と`ObservationService`を渡し、同じ`observations.recording`で操作記録と画像保存を順序化します。入力機器は`protocol`、非同期の`open()`・`send(InputState)`・`close()`を持つ`InputDevice`として受け取り、`SerialInput`をそのまま渡せます。

```python
service = InputService(reader, observations)
await service.connect(SerialInput(config))
grant = await service.begin_run("Aを押して画面を確認", thread_id, turn_id=turn_id)
result = await service.act(grant, parse_sequence(sequence, config.protocol))
await service.finish_run("completed")
await service.close()
await observations.drain()
```

`connect(device)`は旧列を停止して旧接続を閉じ、その後に新機器を開きます。`connect(None)`は切断です。旧機器のclose失敗では所有を保持し、次の機器を開きません。接続中の停止は未開始のopenを取り消し、開始済みopenは収束してから閉じます。接続だけでは操作権を発行しません。

`begin_run`は依頼・run開始の保存成功後に`InputGrant`を返します。開始時に未接続なら、そのrunは入力不可のままです。Grantは同じオブジェクトの対応で確認し、IDをコピーして新しいGrantを作っても使えません。前runの`finish_run`後に次のrunを開始します。開始保存の失敗は`InputServiceError`の`code="STORAGE_ERROR"`です。

`act`は受付時に操作権・接続・映像・列全体を検査します。実行中の別列には`BUSY`を返し、待ち列へ入れません。開始記録の保存後、pressの状態送信・待機・解除とwaitを順番に実行します。各待機は途中停止でき、最後の解除・待機完了後のFrameを共通観測処理へ渡します。符号化までは実行枠を保持し、画像・結果記録の保存完了は待ちません。

`OperationResult.code`が`"OK"`なら列と事後観測が完了しています。失敗はcodeとphaseを持ち、operation_id、完了要素数、入力送出状況、解除状況、確定したObservationを返します。受付拒否はoperation_idなし・入力送出なしです。操作後の画像取得失敗でも、実行済みの入力を取り消した扱いにはしません。事後観測失敗はそのrunの操作権を失効させ、正常な入力接続は維持します。

`stop()`は同期で権限を失効させ、実行中の列を一度だけ取り消します。`await drain_input()`で操作列の送信・解除・符号化の収束を待ちます。接続処理の収束は`close()`が待ちます。正常な停止は接続を維持し、送信・解除失敗では接続を閉じて明示的な再適用を待ちます。`suspend()`は同runの一時保留で、実行中の列を取り消し、新規入力を拒否します。`resume()`は保留を解除しますが、停止済みGrantや取消した列を復活させません。

呼出し側が`act`を取り消した場合も共通停止を行い、呼出し側には`CancelledError`を返します。内部の終了処理はサービスが保持するので、`drain_input`で回収してください。符号化中の停止では完成した画像のIDを中断結果にも記録します。返却後の画像・結果保存失敗は返却済み結果を書き換えず、新しい入力を停止します。

`finish_run(reason)`は先に失効させ、入力終了後にrun終了記録を依頼します。`close()`は進行中の接続処理も回収して機器を閉じます。保存は別の所有者が保持するため、終了時は続けて`observations.drain()`を待ってからwriterとイベントループを閉じてください。これらの内部APIはGUI・MCPへの配線や実機受入を含みません。
