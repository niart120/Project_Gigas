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

`gigas.game_io.input.serial.list_serial_ports()`はpySerialが列挙する各OSのネイティブポートの識別子と説明を返します。機器は開きません。GUI・イベントループ外から呼んでください。OS別の列挙・送信はpySerialに委ねます。WindowsのCH552・9600 baudで短い操作・解除送出・画面変化を確認済みです。PokeCon・3DSの実機、他OS、通信断・長時間運転は未検証です。

`SerialInput(config)`は単一workerを所有します。`await open()`で現在の一覧からport識別子が一意に一致する機器を開き、neutralを送出します。`await send(state)`は完全状態を一度送信します。8N1、フロー制御なし、read timeout 0、write timeoutなし、DTR/RTS有効です。URL・ネットワークシリアルは対応しません。

送信の全byte数が返った場合だけ完了します。部分送出や例外は`SerialInputError`となり、自動再送・flush・ACK待ちは行いません。取消しは開始済みwriteが戻るまで回収し、その間の別の送信・closeは拒否します。保持時間の待機・停止後のneutralは操作実行側が担当します。

接続所有者はI/O終了後に`await close()`を呼びます。close自体は追加のneutralを送信しません。open失敗後もworkerを解放するためcloseが必要です。実ドライバーの停止時間やゲーム側の解除は、これらのAPIの成功だけでは保証しません。

## 記録付き実行API

`gigas.services.input.InputService()`は、一つの入力接続・操作権・実行中の列を所有します。チャットや記録先を作らずに入力接続を開けます。組立側はrun開始時に、その記録先の`ObservationService`と共通の`RecordingService`を渡します。保存失敗のcallbackは診断通知に使い、入力サービスの構築では他サービスのcallbackや会話IDを変更しません。入力機器は`protocol`、非同期の`open()`・`send(InputState)`・`close()`を持つ`InputDevice`として受け取り、`SerialInput`をそのまま渡せます。

```python
service = InputService()
await service.connect(SerialInput(config))
recording = RecordingService(writer)
observations = ObservationService(reader, recording)
grant = await service.begin_run("Aを押して画面を確認", thread_id, observations, recording)
# runtimeから実turn IDを取得した時点で関連付ける。
service.bind_turn(grant, turn_id)
result = await service.act(grant, parse_sequence(sequence, config.protocol))
await service.finish_run("completed")
await service.close()
await observations.drain()
await recording.drain()
```

`connect(device)`は旧列を停止して旧接続を閉じ、その後に新機器を開きます。`connect(None)`は切断です。旧機器のclose失敗では所有を保持し、次の機器を開きません。接続中の停止は未開始のopenを取り消し、開始済みopenは収束してから閉じます。接続だけでは操作権を発行しません。

`begin_run(message, thread_id, observations, recording)`は観測・記録サービスをそのrunのInputGrantへ固定し、開始記録を非同期投入して返します。保存成功を待ちません。入力未接続のrunは入力不可で、停止済みGrantは復活しません。前runのfinish_run後に次のrunを開始します。run終了だけでは入力接続を閉じません。チャット切断や記録先変更でも同じ入力所有者を使い、旧runの観測・記録をdrainしてから旧writerを閉じます。

`bind_turn(grant, turn_id)`は現在のrunへ実turn IDを対応付け、以後の操作開始・run終端記録へ使います。終了済み・別runのGrantにはFalseを返します。停止中のrunへの対応付けでも操作権は復活しません。

`act`は操作権・接続・列全体を検査し、一つの入力列を実行します。入力の受付・継続は映像と保存の成功を条件にしない。観測・保存の失敗は操作権を失効させず、それぞれのエラーとして返す・通知する。入力枠は列と解除の終了時に解放し、直後に最新Frameを選ぶ。符号化中は次の入力を許可する。停止は観測応答を取り消し、開始済みの符号化・保存は観測側で継続する。停止時に観測IDの完成を待たず、後から操作記録へ追記しない。

`OperationResult.code`は要求全体の成否です。受付拒否はoperation_idなし・入力0回です。入力後は完了要素数・送出結果・解除結果を保持します。操作記録のresultは入力列の実行結果を示す。入力完了後の観測失敗・応答取消しでもcompletedを維持し、phase=observeと理由を添える。要求全体のcodeとは区別し、正常に返せた画像だけobservation_idを対応付ける。

`stop()`は同期で権限を失効させ、実行中の列を一度だけ取り消します。`await drain_input()`で操作列の送信・解除と取消した観測要求の収束を待ちます。接続処理の収束は`close()`が待ちます。正常な停止は接続を維持し、送信・解除失敗では接続を閉じて明示的な再適用を待ちます。`suspend()`は同runの一時保留で、実行中の列を取り消し、新規入力を拒否します。`resume()`は保留を解除しますが、停止済みGrantや取消した列を復活させません。

呼出し側による`act`の取消しは、入力実行中なら共通停止を行い、入力完了後の観測待ちならその要求の観測応答だけを取り消します。観測待ちの取消しでは、操作権や後続の入力を維持します。どちらも呼出し側には`CancelledError`を返します。内部の終了処理はサービスが保持するので、`drain_input`で回収してください。

run・操作の開始と終端は同じ記録窓口へ順に非同期投入します。保存完了を入力開始の前提にせず、保存失敗でも入力を止めません。記録が欠けたまま操作される場合を許容し、失敗は診断へ残します。

`finish_run(reason)`は先に失効させ、入力終了後にrun終了記録を依頼します。`close()`は進行中の接続処理も回収して機器を閉じます。保存は別の所有者が保持するため、終了時は続けて`observations.drain()`で符号化を回収し、`recording.drain()`で保存を回収してからwriterとイベントループを閉じてください。符号化失敗時も保存の回収は必要です。これらの内部APIはGUI・MCPへの配線や実機受入を含みません。

保存済み入力列には`parse_sequence(value)`で共通の形式・値域だけを検査できます。実行受付ではprotocolを指定して対応能力も検査します。停止時も開始済みpress・neutralの送信結果を回収し、全byte成功は送出済みとして記録します。
