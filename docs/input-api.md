# 入力状態と操作列API

`gigas.game_io.input.models.parse_sequence(value, protocol)` は、JSONの`sequence`配列全体を検査し、不変の`tuple[InputStep, ...]`へ変換します。機器への送信は行いません。プロトコルは`gigas.game_io.input.config.get_protocol("ch552")`、`"pokecon"`、`"3ds"`から選びます。

```python
from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState, parse_sequence
from gigas.game_io.input.protocol import encode_state

protocol = get_protocol("ch552")
steps = parse_sequence([
    {"kind": "press", "buttons": ["A"], "duration_ms": 100},
    {"kind": "wait", "duration_ms": 250},
], protocol)
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

`gigas.game_io.input.serial.list_serial_ports()`はpySerialが列挙する各OSのネイティブポートの識別子と説明を返します。機器は開きません。GUI・イベントループ外から呼んでください。OS別の列挙・送信はpySerialに委ねます。Linux/macOSの実接続・実機動作は未検証です。

`SerialInput(config)`は単一workerを所有します。`await open()`で現在の一覧からport識別子が一意に一致する機器を開き、neutralを送出します。`await send(state)`は完全状態を一度送信します。8N1、フロー制御なし、read timeout 0、write timeoutなし、DTR/RTS有効です。URL・ネットワークシリアルは対応しません。

送信の全byte数が返った場合だけ完了します。部分送出や例外は`SerialInputError`となり、自動再送・flush・ACK待ちは行いません。取消しは開始済みwriteが戻るまで回収し、その間の別の送信・closeは拒否します。保持時間の待機・停止後のneutralは操作実行側が担当します。

接続所有者はI/O終了後に`await close()`を呼びます。close自体は追加のneutralを送信しません。open失敗後もworkerを解放するためcloseが必要です。実ドライバーの停止時間やゲーム側の解除は、これらのAPIの成功だけでは保証しません。
