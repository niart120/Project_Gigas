# 記録付き観測API

Windowsで、取得済み画像をPNGとJSONLへ記録するPython APIです。アプリでは[プロジェクト操作とチャット](../../guides/codex.md)に結合し、MCP経由で同じ画像をCodexへ返します。

## 単独のゲームI/O記録

`gigas.storage.records.GameIoRecordWriter`は、呼出し側が指定したフォルダーへゲームI/Oと本文を記録する同期APIです。Project・Codexの会話や接続は不要です。呼出し側が保存先と開始・終了を管理し、writerは`events.jsonl`と`observations/<observation-id>.png`を保存します。`.gigas.lock`は同じ保存先への書込所有権に使います。

`GameIoRecordWriter.create(path)`は、親が既にあり、保存先自体は存在しない場合に作成します。`open(path)`は既存の保存先を開き、events.jsonl・observationsがなければ作成します。祖先フォルダーの作成、既存資料の初期化、過去ログの読込・検査は行いません。どちらも成功時はwriterを返し、使用後は`close()`で所有権を解放してください。同期処理なのでGUIスレッドやイベントループ上で直接呼ばないでください。

次の例は機器を使わず、一時フォルダーへ提出本文・入力開始・観測・入力結果・確定本文を保存します。入力結果は例示用の値で、物理入力を行っていません。

```python
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gigas.game_io.capture.models import Frame
from gigas.game_io.input.models import InputStep
from gigas.game_io.observation import Observation
from gigas.storage.input_events import OperationFinished, OperationStarted
from gigas.storage.records import GameIoRecordWriter

frame = Frame(
    bgr=bytes([255, 0, 0]) * (16 * 16),
    width=16,
    height=16,
    generation=1,
    sequence=1,
    captured_at=datetime.now(UTC),
    monotonic_ns=0,
)
with TemporaryDirectory(dir=Path.cwd()) as temporary:
    with closing(GameIoRecordWriter.create(Path(temporary) / "records")) as writer:
        writer.record_dialogue("dialogue.submitted", "現在の画面を確認してください")
        writer.record_input(OperationStarted("operation-1", (InputStep("wait", 100),)))
        observation = Observation.from_frame(frame)
        writer.record(observation)
        writer.record_input(
            OperationFinished(
                "operation-1", "completed", 1, observation_id=observation.observation_id
            )
        )
        writer.record_dialogue("dialogue.message", "画面を確認しました。")
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes
```

各メソッドは成功時に`None`を返します。`record_dialogue(kind, text)`のkindは`dialogue.submitted`または`dialogue.message`です。提出の受付結果や最終回答をwriterが判定することはありません。`record_input(event)`の開始と結果はoperation_idで対応付け、画像があればobservation_idを残します。`record(observation)`は元のPNG bytesを再符号化せずに保存し、同じIDの画像が既にあれば上書きを拒否します。

JSONLの共通項目は`schema_version: 1`、行組立時のUTC時刻`recorded_at`、`type`、`payload`です。本文payloadは`{"text": "本文"}`だけです。入力開始payloadはoperation_idと操作配列sequence、結果payloadはoperation_id・result・completed_steps・input_sent・neutral、指定されたobservation_idと空でないphase・reasonです。観測payloadはobservation_id・captured_at・image_path・image・source_frameを持ち、imageとsource_frameはそれぞれwidth・heightだけです。captured_atは元Frameの取得時刻を保持します。イベント連番、Project・thread・turn・eventのID、取得サービスのgeneration・sequence、MIMEは保存しません。

追記は標準ライブラリでJSON一行と改行を末尾へ書きます。既存の壊れた行や末尾に改行がなくても検査・修復せず、そのまま追記します。その結果、新しい行も読めなくなる可能性があります。復旧は利用者がwriter終了後に行ってください。記録のJSON化・UTF-8変換、画像公開、JSONLのopen・write・closeの失敗や短いwriteは`RecordError`で通知し、次の新しい記録は受け付けます。失敗した記録を自動再送せず、JSONL失敗前に公開した画像も残します。

同じwriterへの同期処理の重複・書込中のcloseは`RecordBusyError`（RecordErrorの派生型）になります。呼出し側で直列化し、処理を回収してからcloseしてください。close後の記録は拒否し、closeの再呼出しは何もしません。OSロックは協調するwriter間の排他であり、画像・ログの閲覧は妨げません。稼働中の原記録の編集とロックファイルの削除は避けてください。保存は通常のwrite/closeで、fsyncや画像・JSONLの一括更新は保証しません。

## アプリの記録先

GIGASは作業フォルダー配下の`records/<thread-id>/`を記録先にします。作業場所の選択、会話との対応、本文の保存対象は[保存仕様](storage.md)を参照してください。writer自身はProjectや会話を管理せず、呼出し側が保存先と記録する値を決めます。

## 画像と入力結果

`Observation.from_frame(frame, resolution="full_hd")`は全画面を比率維持で縮小し、一度だけPNGへ符号化します。`resolution`は`original`、`full_hd`（1920×1080の枠、既定）、`hd`（1280×720の枠）、`sd`（720×480の枠）です。小さい画像は拡大せず、切り抜きやプレビューの黒帯も加えません。入力は幅16〜3840・高さ16〜2160、符号化後は32 MiB以下です。

`Observation`はPNG bytes、出力寸法、`mime_type == "image/png"`、観測ID、元取得時刻`captured_at`、`source_frame`を持ちます。観測IDは`obs-`にUUID4を続けた値で、同じFrameから再び生成すれば新しいIDになります。`image_path`は記録先からの相対パスです。画像の保存先だけはIDを確認して固定し、その他の内容の意味・実在・ゲーム内成否は呼出し側が扱います。

`source_frame`の世代・連番・元寸法は取得サービスの情報で、元FrameやBGR画像本体を保持しません。JSONLには元寸法だけを保存します。[MCP応答](../interfaces/observation-mcp.md)とはメタデータの項目が異なりますが、PNG bytesと観測IDは共通です。`captured_at`は元FrameのPC取得時刻であり、カメラの撮影時刻や入力反映時刻は保証しません。

入力結果の`result`は`completed`・`interrupted`・`failed`、`input_sent`は`none`・`sent`・`unknown`、`neutral`は`not_needed`・`sent`・`unconfirmed`です。後二者の既定値はnone・not_needed。`completed_steps`は解除送信または待機を完了した要素数で、ゲーム内の成功数ではありません。入力完了後の観測失敗では`result: "completed"`と`phase: "observe"`が併存し得ます。

操作はoperation_id、画像はobservation_idで対応付けます。行順は追記順で、観測行が入力結果より先に残る場合もあります。行の隣接関係や時刻から特定のユーザー指示への帰属を推定しません。

## 最新画像を非同期で観測する

`ObservationService(reader, recording, resolution="full_hd", on_save_error=...)`へ、既存の`CaptureService.reader()`と共通の`RecordingService(writer)`を渡します。サービスは機器を開始・再接続せず、writerも開き直しません。

`await service.observe()`はsnapshotを一度読み、`phase == "streaming"`で有効な画像がある場合に採用します。要求前の最新キャッシュを使い、新しいFrameを待ちません。採用後に取得が停止しても、その画像の符号化・保存は続けます。

加工・符号化は別スレッドで行い、完成した同じObservationを返却と非同期保存へ渡します。保存中も次の観測を返せます。`BUSY`は加工・符号化が進行中の場合だけです。`resolution`の変更は同じイベントループで行い、以後に開始する観測へ反映します。観測失敗のcodeは`DEVICE_UNAVAILABLE`・`BUSY`・`OBSERVATION_ENCODING_ERROR`です。

`RecordingService`は一つのwriterを借り、画像・入力・本文の保存だけを直列化します。同じwriterに複数の保存サービスを作らないでください。`submit_observation(observation, on_error=...)`、`submit_input(event)`、`submit_dialogue(kind, text)`は保存を待たずに戻ります。観測・入力・停止の経路も保存完了を待ちません。

保存失敗は`recording.on_failure(error)`へ通知し、観測固有の`on_save_error(observation, error)`にも通知します。観測成功や完了済み入力を取り消しません。コールバックは同じイベントループで速やかに戻してください。省略時の詳細はloggingへ出力します。`observe()`の成功直後に画像ファイルがある保証はありません。

### 取消しと終了

観測要求を取り消しても、投入済みの符号化・保存は完了または失敗まで続きます。新規要求を止め、符号化を`await service.drain()`、保存を`await recording.drain()`で回収してからwriterを閉じます。保存drainの完了は全件の保存成功を意味せず、失敗は通知やログで確認します。

```python
import asyncio

from gigas.game_io.observation import ObservationError
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.records import GameIoRecordWriter


async def close_records(
    service: ObservationService, recording: RecordingService, writer: GameIoRecordWriter
) -> None:
    encoding_error = None
    try:
        await service.drain()
    except ObservationError as error:
        encoding_error = error
    await recording.drain()
    await asyncio.to_thread(writer.close)
    if encoding_error is not None:
        raise encoding_error
```

回収待機自体の取消しでは所有権を捨てず、同じ所有者でdrainをやり直してから閉じます。GIGASは切断・終了でMCP受付を閉じ、開始済み処理と保存を回収してwriterを閉じます。画像・記録自体は残します。

画像は同じフォルダーの一時ファイルをcloseしてから上書きせず確定し、その後JSONLへ追記します。画像の欠損検査、余剰画像の削除、ネットワーク共有と外部編集の競合解決は扱いません。異常終了では直近の画像・記録が欠ける場合があります。
