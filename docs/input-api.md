# 入力状態と操作列API

`gigas.game_io.input.models.parse_sequence(value)`は、JSONの`sequence`配列を読み、不変の`tuple[InputStep, ...]`へ変換します。各値はInputState/InputStepの構築時に共通の条件を満たします。解析時は機器を選択せず、送信もしません。実行時の機器能力は`InputService.start()`が列全体へ確認します。プロトコルは`gigas.game_io.input.config.get_protocol("ch552")`、`"pokecon"`、`"3ds"`から選びます。

```python
from gigas.game_io.input.models import parse_sequence

steps = parse_sequence(
    [
        {"kind": "press", "buttons": ["A"], "duration_ms": 100},
        {"kind": "wait", "duration_ms": 250},
    ]
)
```

`press`は完全状態を保持してから解除する入力、`wait`は未押下での待機を表します。時間は正の整数ミリ秒です。配列の要素数、入力時間、合計時間の固定上限は設けません。無限生成器、期限なしhold、未知の項目、小数・文字列・boolの時間や座標は`InvalidSequenceError`で拒否します。未知・非対応ボタンと非対応touchは、サービスの開始前検査で列全体を`INVALID_SEQUENCE`として拒否します。

ボタンは重複なしの名前配列です。`UP/RIGHT/DOWN/LEFT`は隣接2方向まで同時指定でき、反対方向は拒否します。左右stickは`{"x": -100..100, "y": -100..100}`、右と下が正で、省略時は中心です。3DSのtouchは`{"x": 0..319, "y": 0..239}`、省略またはnullで解除を表し、映像の座標とは独立しています。入力のないpressは拒否します。

| プロトコル | ボタン | touch |
|---|---|---|
| CH552 / PokeCon | A/B/X/Y/L/R/ZL/ZR/HOME、PLUS/MINUS/LS/RS/CAPTURE、方向 | 非対応 |
| 3DS S2/T3 New Firmware | A/B/X/Y/L/R/ZL/ZR/HOME、START/SELECT、方向 | 対応 |

`InputState`は直接構築する場合も、buttonsと座標の不変tuple・要素型・整数範囲・重複・反対方向を検査します。neutralを許し、touchの`(0, 0)`は接触として扱います。`InputStep`は正の整数時間、press/wait種別、InputState型、pressは入力あり・waitはneutralという整合を保証します。生成後はフィールドを変更できません。どちらの構築も機器能力は検査しません。

`encode_state(state, protocol)`には、選択機器に適合する生成済みInputStateを渡します。共通条件や機器能力を再検査せず、毎回完全なフレームを作り、以前の状態を保持しません。単独利用で能力確認が必要なら`validate_state(state, protocol)`を明示的に呼べます。非対応状態を単独encoderが必ず拒否する保証はありません。`InputStep.to_json()`は記録に使える独立したJSON値を返します。byte列への変換成功は実機の受信・操作・解除の確認を意味しません。

## シリアル接続API

`resolve_serial_config(port, protocol, baudrate)`は設定を接続用の値へ解決します。空portなら`None`、baudrateが`None`ならプロトコルの既定値になります。候補と検証は`PROTOCOLS`を共用し、対応しない値は`ValueError`になります。

`gigas.game_io.input.serial.list_serial_ports()`はpySerialが列挙する各OSのネイティブポートの識別子と説明を返します。機器は開きません。GUI・イベントループ外から呼んでください。OS別の列挙・送信はpySerialに委ねます。WindowsのCH552・9600 baudで短い操作・解除送出・画面変化を確認済みです。PokeCon・3DSの実機、他OS、通信断・長時間運転は未検証です。

`SerialInput(config)`には`resolve_serial_config()`で解決した設定を渡します。接続側は設定を再解決せず、単一workerで`await open()`を実行し、現在の一覧からport識別子が一意に一致する機器を開いてneutralを送出します。`await send(state)`にはその機器に適合する生成済みInputStateを渡し、完全状態を一度送信します。8N1、フロー制御なし、read timeout 0、write timeoutなし、DTR/RTS有効です。URL・ネットワークシリアルは対応しません。

送信の全byte数が返った場合だけ完了します。部分送出や例外は`SerialInputError`となり、自動再送・flush・ACK待ちは行いません。取消しは開始済みwriteが戻るまで回収し、その間の別の送信・closeは拒否します。保持時間の待機・停止後のneutralは操作実行側が担当します。

接続所有者はI/O終了後に`await close()`を呼びます。close自体は追加のneutralを送信しません。open失敗後もworkerを解放するためcloseが必要です。実ドライバーの停止時間やゲーム側の解除は、これらのAPIの成功だけでは保証しません。

## 入力実行API

`gigas.services.input.InputService()`は、一つの入力接続と実行中の操作列を所有します。観測・記録先・会話IDは受け取りません。入力機器は`protocol`、非同期の`open()`・`send(InputState)`・`close()`を持つ`InputDevice`として渡し、`SerialInput`をそのまま使えます。

```python
import asyncio

from gigas.game_io.input.serial import SerialInput
from gigas.services.input import InputService, OperationResult

service = InputService()
await service.connect(SerialInput(config))
try:
    started = service.start(steps)
    if isinstance(started, OperationResult):
        result = started  # 拒否。入力は送られていない。
    else:
        operation_id, task = started
        result = await asyncio.shield(task)
finally:
    service.stop()
    await service.drain_input()
    await service.close()
```

`start(sequence)`へはparserの戻り値、または生成済みInputStepの非空tupleを渡します。サービスは共通条件や非空条件を検査し直しません。同期的に受付とtask登録を行い、受理時は`(operation_id, task)`、拒否時はoperation_idのない`OperationResult`を返します。別の列が解除中まで含めて実行中なら`BUSY`、機器が使用不能なら`INPUT_DISABLED`です。待ち行列や自動再送はありません。機器能力は入力前に列全体へ確認します。

返されたtaskは`asyncio.shield`で待ちます。要求を取り消す場合は、捕捉したそのtaskが未完了かつ取消し未要求のときだけ`cancel()`し、同じtaskから送信・解除の結果を回収します。後から始まった別の操作へ取消しを送ってはいけません。開始前の取消しではtaskが`CancelledError`で終わり、入力は送られません。

`OperationResult`は入力だけの結果です。`code`、`phase`、`operation_id`、`completed_steps`、`input_sent`、`neutral`、`message`を持ち、観測画像は含みません。`code="OK"`は列と解除の処理が完了した意味で、実機のACKやゲームへの反映を保証しません。開始済みpress・neutralの全byte送出が確認できた場合は、取消し中でも`sent`として結果へ残します。

`stop()`は現在の操作taskへ一度だけ取消しを要求し、捕捉した`asyncio.Task[OperationResult]`を返します。操作がなければ`None`です。停止呼出し側がこのtaskを保持すれば、通常の完了処理が入力サービスの現在枠を解放した後も、対象の解除結果を回収できます。`await drain_input()`は開始済みwriteと必要なneutral送出まで待ち、`OperationResult`を返します。操作なし・coroutine開始前の取消しでは`None`です。この待機の取消しで入力taskを放棄しません。正常な停止後は、同じ入力接続で新しい操作列を開始できます。会話全体の停止中に新規要求を拒否するのは、会話の受付側の責務です。

`connect(device)`は旧列を止めて回収し、旧機器を閉じてから新機器を開きます。`connect(None)`は切断です。旧機器のclose失敗では所有を保持し、次の機器を開きません。操作停止は接続処理を取り消しません。`close()`は未開始のopenを抑止し、開始済みopenも回収して機器を閉じます。送信・解除失敗後は機器を使用不能とし、明示的な接続の適用で復旧します。

## 観測・記録との組合せ

会話のツール応答組立側が、受理した操作の開始記録を提出し、入力taskの完了後に`ObservationService.observe()`で最新画像を取得します。画像を選ぶ前に次の入力が始まる場合があります。Frameの取得時刻・識別情報を保持し、入力が画面へ反映された保証にはしません。

画像・保存の失敗は入力結果を変更しません。操作の開始・結果はoperation_idで対応付け、既知のthread_idと取得できたobservation_idを記録します。機器操作を特定のユーザー指示やturnへ推定で所属させません。保存完了を入力開始・応答・停止の前提にしません。

観測応答の取消しでは、開始済み符号化・保存はそれぞれの所有者が保持します。資源終了時は、入力を止めて要求を回収し、`observations.drain()`と`recording.drain()`を終えてからwriterとイベントループを閉じます。観測の失敗があっても保存の回収は必要です。

入力・MCPに加え、会話とGUIの配線は模擬app-server・機器、Qt、実HTTPで検証しています。実モデルからCH552への短いゲーム操作・中断も確認済みです。GUIから実機までの通し操作、他の入力機器・OS、長時間運転は未検証です。詳細は[実接続の確認](codex-verification.md)を参照してください。
