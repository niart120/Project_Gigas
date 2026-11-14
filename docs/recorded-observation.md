# 記録付き観測API

Windowsで、取得済み画像をPNGとJSONLへ記録するPython APIです。アプリでは[プロジェクト操作とチャット](codex-gui.md)に結合し、MCP経由で同じ画像をCodexへ返します。

入口は`gigas.storage.project.ProjectWriter`と`gigas.services.observation.ObservationService`です。共通の非同期保存は`gigas.services.recording.RecordingService`が担当します。元画像には[映像取得API](video-api.md)の不変な`Frame`を使います。

`ProjectWriter`はアプリが原記録を書き込むための所有者です。アプリが現在の記録先に一つ保持し、観測サービスはその参照を借ります。プロジェクト未選択のチャットにもアプリ管理の記録先を用意します。観測ごとに作成・終了するものではなく、MCP接続やCodexの会話自体も管理しません。

観測サービスは加工・符号化した画像を返し、同じ画像・取得時刻・IDを別の非同期処理で保存します。保存開始・完了は応答の前提ではありません。[MCP公開部](observation-mcp.md)は同じObservationを応答へ包装します。会話はCodexの永続threadとして保持し、原記録には実thread IDとの対応を保存します。公開本文・推論要約・活動などの対話記録も共通の`RecordingService`へ非同期投入します。解釈を待って画像保存を遅らせたり、保存済みの原観測を解釈結果で書き換えたりしません。Codexは[接続時に許可された`memory/`](codex.md)を編集できます。

## 一枚を保存する

`ProjectWriter.create(path)`は、親ディレクトリが存在し、保存先自体は存在しない場合にだけ作成します。既存プロジェクトには`ProjectWriter.open(path)`を使います。どちらも同期処理なので、GUIスレッドやイベントループ上で直接実行しないでください。

次の例は機器を使わず、16×16の青いBGR画像を一時プロジェクトへ保存します。書込可能な作業ディレクトリで`uv run python example.py`を実行してください。一時ディレクトリは例の終了時に削除されます。

```python
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gigas.game_io.capture.models import Frame
from gigas.game_io.observation import Observation
from gigas.storage.project import ProjectWriter

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
    writer = ProjectWriter.create(Path(temporary) / "project")
    try:
        observation = Observation.from_frame(frame)
        writer.record(observation)
        saved_image = writer.path / observation.image_path
        assert saved_image.read_bytes() == observation.png_bytes
        assert observation.captured_at == frame.captured_at
    finally:
        writer.close()
```

`Observation.from_frame(frame, resolution="full_hd")`が全画面を比率維持で縮小してPNGへ一度だけ符号化し、観測IDを発行します。`resolution`は`original`（元寸法）、`full_hd`（1920×1080の枠、既定）、`hd`（1280×720の枠）です。小さい画像は拡大しません。`writer.record(observation)`はそのPNGとJSONL一行を保存し、成功時は`None`を返します。保存時の再符号化は行いません。`observation_id`は`obs-`にUUID4を続けた値、`image_path`は`observations/<observation_id>.png`です。同じFrameから再び`Observation`を作れば新しいIDになります。既に保存した同じ観測を再度保存しようとすると、既存画像の上書きを拒否します。

入力画像の対応範囲は幅16〜3840、高さ16〜2160、符号化後32 MiB以下です。切り抜き・拡大を行わず、プレビューの黒帯や表示倍率も持ち込みません。`Observation.from_frame()`は加工・符号化だけを行い、プロジェクトへ保存しません。

`Observation`は確定した`png_bytes`、`width`・`height`、固定の`mime_type == "image/png"`と、観測ID・元取得時刻`captured_at`・`source_frame`を持ちます。`source_frame`は`FrameSource`型の世代・連番・元幅・元高さだけで、元FrameやBGR画像本体は保持しません。保存と配送には同じPNG bytesを使用できます。

## 最新画像を非同期で観測する

`ObservationService(reader, recording, resolution="full_hd", thread_id=None, on_save_error=...)`には、既存の`CaptureService.reader()`と、組立側が生成した共通の`RecordingService(writer)`を渡します。サービスが機器を開始・再接続したり、writerを開き直したりすることはありません。

`await service.observe()`はsnapshotを一度読み、`phase == "streaming"`で画像がある場合に採用します。呼出しより前に取得した最新キャッシュを使い、新しいフレームを待ちません。採用前の取得停止・エラーでは失敗し、採用後に取得が停止しても、その画像の保存を続けます。`captured_at`は元Frameの時刻を保持します。撮像時刻、機器内部の遅延、操作後の画面であることは保証しません。

加工・符号化を別スレッドで行い、完成した同じ`Observation`を返却と非同期保存へ渡します。保存中も次の観測を取得・符号化して返せます。`BUSY`になるのは画像の加工・符号化自体が進行中の場合だけです。`resolution`の変更は同じイベントループ上で行い、変更後に開始する観測へ反映します。実行中の画像は変更しません。

`thread_id`を指定すると、その実会話IDを観測記録へ関連付けます。観測開始時にIDを確定し、符号化・保存中に属性を変更しても先行観測のIDを差し替えません。単独Python観測では`None`として会話参照を省略できます。

`recording`は組立側が所有する共通の保存窓口です。観測・会話・入力の記録をこの一つの窓口へ渡し、保存処理だけを`asyncio.Lock`で直列化します。このロックを画像の加工・返却では待ちません。同じwriter用に別の記録サービスを作らないでください。独自キュー、保存見送り、保持上限は設けていません。プロセスロックによってアプリ内の並列書込が禁止されるわけではありません。

保存失敗は`on_save_error(observation, error)`へ通知し、観測成功を取り消しません。コールバックは同じイベントループで呼ばれるため、速やかに戻してください。省略時は観測IDと保存先をログへ記録します。`observe()`の成功直後にファイルが存在する保証はなく、障害時に未保存画像が失われる場合があります。

### 取消しと終了

`observe()`の要求を取り消しても、投入済みの符号化・保存は完了または失敗まで続きます。新規要求を止めた後、`await service.drain()`で符号化を回収し、`await recording.drain()`で保存を回収します。観測サービスは共有保存の寿命を所有しません。最後の符号化が失敗しても保存を回収してからwriterを閉じてください。

保存エラーはcallbackまたはログで別途通知し、`recording.drain()`の完了は保存成功を意味しません。待機自体の取消しでは資源を解放せず、同じ所有者で回収をやり直します。要求取消しによって保存済み画像を削除しません。

```python
import asyncio

from gigas.game_io.observation import ObservationError
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.project import ProjectWriter


async def close_project(
    service: ObservationService, recording: RecordingService, writer: ProjectWriter
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

プロジェクト未選択のチャットは切断時にwriterを閉じ、記録自体は残します。明示選択したプロジェクトのwriterは切断後も保持し、保存先の切替やアプリ終了で閉じます。

## 操作後の画像

操作列の最後の解除・末尾waitが完了した後に、単独observeと同じ処理で最新キャッシュを一度取得する。新しいFrameを待たず、操作前や前回観測と同じFrameも許容する。画像取得時刻やゲームへの入力反映は保証しない。

`service.observe(thread_id=...)`で操作の会話IDを明示できます。省略時はサービスの会話IDを使います。入力枠は列と解除の終了時に解放するため、画像を選ぶ前や符号化中に次の入力が始まる場合があります。共通停止は入力列の解除・回収を待ち、画像や保存の完成は待ちません。観測要求の取消し・期限は当該応答だけを取り消し、開始済み符号化・保存と別の入力列を維持します。操作結果は応答組立の終了時に一度記録し、取得できた観測IDだけを対応付けます。生成したPNGは応答と非同期保存で共用します。

## 保存形式と読込

プロジェクトには`project.json`、`events.jsonl`、`observations/`、OSロック用の`.gigas.lock`を置きます。管理情報はスキーマ版、プロジェクトID、作成UTC時刻です。ログの共通項目はスキーマ版、イベントID、追記順、記録UTC時刻、イベント種別、プロジェクトID、payloadです。

`observation.recorded`のpayloadには、観測ID、元Frameの取得UTC時刻、画像相対パス、元Frameの世代・連番・幅・高さを記録します。`image`には確定したPNGの`mime_type`・`width`・`height`を記録し、元寸法と区別します。世代・連番は取得サービスの寿命内の補助情報で、観測の永続識別には観測IDを使います。記録時刻と画像取得時刻は別です。

`analysis`欄は生成しません。Codexの解釈を後から原観測へ埋める欄も設けません。過去payloadは共通形式のまま読み、移行・再書込み・新旧の二重書込みは行いません。

`open()`と`reload()`は管理情報とJSONLを検査します。未知のイベント種別や余剰項目は共通形式が正常なら保持し、連番の欠番も許容します。次の記録は最後のsequenceに1を加えて追記します。過去の画像を全件読み込む検査はせず、過去画像の欠損だけでは新規記録を拒否しません。

未知のスキーマ版、不正なUTF-8・JSON・共通項目・追記順、改行で終わらない末尾行は読込失敗になります。payloadは意味を再検査せず、過去の入力列・画像パス・旧イベントも値のまま保持します。JSONLでは問題行と、その前の正常に読めた行数をエラーに示します。原本の切り詰め、自動修復、不完全なプロジェクトの再初期化は行いません。

## 会話と観測の対応

`writer.record_thread(thread_id)`は実際のCodex会話IDを記録します。アプリは`recording.submit_thread(thread_id)`で非同期投入し、保存を待たずにturnまたはGoalの要求へ進みます。失敗は既存の通知へ残し、入力の受付とは分離します。

`writer.record(observation, thread_id=thread_id)`は同じIDを`observation.recorded`のpayloadへ付けます。IDは空・空白のみでない文字列として扱い、UUID形式に限定しません。writerは現在の会話やID索引を持ちません。単独Python観測では会話がないため省略できます。会話の開始記録も画像と同じJSONLへ採番して追記し、追記結果が不明なら再読込まで次の追記を止めます。

## 入力の開始・結果を記録する

`gigas.storage.input_events`の変更不能な値を`writer.record_input(event)`へ渡すと、同じJSONLへ入力イベントを追記します。

| 型 | 保存する内容 |
|---|---|
| `OperationStarted(operation_id, sequence, thread_id, turn_id=None)` | 検証済み`InputStep`のタプルを、ボタン・スティック・タッチ・指定時間を持つJSON配列として`input.operation_started`へ記録 |
| `OperationFinished(operation_id, result, completed_steps, ...)` | 完了要素数、結果、確定した`observation_id`を`input.operation_finished`へ記録 |

`OperationFinished.result`は`completed`・`interrupted`・`failed`です。中断・失敗時は`phase`と`reason`を指定します。`input_sent`は`none`・`sent`・`unknown`、`neutral`は`not_needed`・`sent`・`unconfirmed`で、送出済みと実機反映を区別します。省略値はそれぞれ`none`・`not_needed`です。`observation_id`は確定した場合だけ指定し、画像保存が後から失敗しても消しません。`turn_id`は実値が判明している場合だけ指定します。port・baud・protocolは入力イベントへ含めません。

入力と観測の応答を組み立てる会話側の処理が、共通の`recording`へ記録を提出します。入力サービス自身は記録先や会話IDを持ちません。開始・結果ともに、変更不能なイベントを`recording.submit_input(event)`で投入します。入力開始・停止・解除は保存完了を待ちません。受付済み保存は窓口が所有し、入力要求の取消し後も継続します。保存の成否で入力受付を切り替えません。操作記録はoperation_idで対応付け、特定のユーザー指示やturnへの帰属を推定しません。

`recording.on_failure`へ設定した関数は、会話・入力・画像のすべての保存失敗を`Exception`として受け取ります。アプリは失敗を診断へ残し、保存失敗だけで入力を止めません。観測固有の`on_save_error`も通知されます。コールバックは同じイベントループで速やかに戻してください。`submit_input`は保存を待たないため、後で起きた保存例外を呼出側へ返しません。同期の`writer.record_input`を直接呼ぶ場合は、その呼出しから保存例外が返ります。

通常終了は新しい要求を止め、`await service.drain()`で観測の符号化を回収し、続けて`await recording.drain()`で受付済み保存を回収してからwriterを閉じます。符号化に失敗した場合も保存の回収は必要です。`RecordingService`を単独で使用する場合は保存の回収だけを行います。`recording.drain()`は保存を再試行せず、保存例外を呼出側へ再送出しません。失敗はコールバックとログで確認します。回収完了は全件の保存成功を意味しません。

## 保存失敗と書込所有権

| 結果 | 保存物と次の操作 |
|---|---|
| PNG符号化の失敗 | 観測が失敗します。画像とJSONLは書きません |
| 画像保存の失敗 | 観測成功は変えず、保存失敗を通知します。JSONLへまだ触れていないため、writerは引き続き使用できます |
| JSONLの追記・flush・fsyncの失敗 | 公開済みPNGと原記録を保持し、次の追記を拒否します。明示的な`reload()`で検査が通るまで再開できません |
| 保存完了後の要求取消し | 観測記録は保持します。保存成功を応答配送の成功とは扱いません |

観測エラーの`code`は`DEVICE_UNAVAILABLE`、`BUSY`、`OBSERVATION_ENCODING_ERROR`です。作成・読込・保存の失敗は`ProjectError`、同期処理中の二重書込・再読込・終了はその派生型`ProjectBusyError`です。エラー後の自動再試行はありません。

アプリの原記録writerは、現在の記録先に一つです。`.gigas.lock`のWindows OSファイルロックにより、同じ規約を使う別writerの取得を拒否します。`.gigas.lock`が存在するだけでは使用中とは判断せず、プロセス終了後は再度開けます。利用中にロックファイルを削除したり、アプリ管理の`project.json`・`events.jsonl`・`observations/`を別経路から変更したりしないでください。ネットワーク共有と外部編集の競合解決には対応していません。

このロックは、画像・ログの読取や別ファイルの`memory/`編集を禁止するものではありません。対話記録も同じアプリ所有writerを使い、MCPや通知処理がそれぞれ別writerを開く構成にはしません。アプリのCodex接続は`memory/`だけを書込先にしたworkspace-writeで動作します。原記録はアプリが書き込み、Codexへは読取用に渡します。接続条件と確認範囲は[Codex接続API](codex.md)を参照してください。

画像は同じディレクトリの一時ファイルへ書き込み、flush・fsync後、既存画像を上書きせず確定します。その後にJSONLへ追記・flush・fsyncします。電源断時にも画像とログが必ず一括で更新される保証はありません。

事後観測の`thread_id`には受付済みrunの会話IDを渡します。単独観測用の`service.thread_id`を書き換えず、操作に対応する記録文脈を保持します。入力記録の再読込は共通入力形式を検査し、現在の通信機器で実行できるかは検査しません。
