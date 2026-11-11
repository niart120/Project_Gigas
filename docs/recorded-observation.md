# 記録付き観測API

Windowsで、取得済み画像をPNGとJSONLへ記録するPython APIです。保存先を選ぶGUIとMCP・Codexへの画像配送は、現在のアプリには接続されていません。

入口は`gigas.storage.project.ProjectWriter`と`gigas.services.observation.ObservationService`です。元画像には[映像取得API](video-api.md)の不変な`Frame`を使います。

`ProjectWriter`はアプリが原記録を書き込むための所有者です。プロジェクト選択中はアプリが一つ保持し、観測サービスはその参照を借ります。観測ごとに作成・終了するものではなく、MCP接続やCodexの会話自体も管理しません。

観測サービスは加工・符号化した画像を返し、同じ画像・取得時刻・IDを別の非同期処理で保存します。保存開始・完了は応答の前提ではありません。[MCP公開部](observation-mcp.md)は同じObservationを応答へ包装します。アプリ内の接続と終了にも結合しています。Codexが後で画像を解釈した公開応答や対話履歴は、受信した内容をアプリが別の記録として保存する対象です。解釈を待って画像保存を遅らせたり、保存済みの原観測を解釈結果で書き換えたりしません。対話履歴の保存とCodexによる`memory/`編集は、現在のAPIには未接続です。

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

`ObservationService(reader, writer, resolution="full_hd", thread_id=None, on_save_error=...)`には、既存の`CaptureService.reader()`とアプリが所有するwriterを渡します。サービスが機器を開始・再接続したり、writerを開き直したりすることはありません。

`await service.observe()`はsnapshotを一度読み、`phase == "streaming"`で画像がある場合に採用します。呼出しより前に取得した最新キャッシュを使い、新しいフレームを待ちません。採用前の取得停止・エラーでは失敗し、採用後に取得が停止しても、その画像の保存を続けます。`captured_at`は元Frameの時刻を保持します。撮像時刻、機器内部の遅延、操作後の画面であることは保証しません。

加工・符号化を別スレッドで行い、完成した同じ`Observation`を返却と非同期保存へ渡します。保存中も次の観測を取得・符号化して返せます。`BUSY`になるのは画像の加工・符号化自体が進行中の場合だけです。`resolution`の変更は同じイベントループ上で行い、変更後に開始する観測へ反映します。実行中の画像は変更しません。

`thread_id`を指定すると、その実会話IDを観測記録へ関連付けます。観測開始時にIDを確定し、符号化・保存中に属性を変更しても先行観測のIDを差し替えません。単独Python観測では`None`として会話参照を省略できます。

現在の実装は同期writerを再利用し、保存処理だけを`asyncio.Lock`で直列化します。このロックを観測の加工・返却では待ちません。独自キュー、保存見送り、保持上限は設けていません。プロセスロックによってアプリ内の並列書込が禁止されるわけではありません。

保存失敗は`on_save_error(observation, error)`へ通知し、観測成功を取り消しません。コールバックは同じイベントループで呼ばれるため、速やかに戻してください。省略時は観測IDと保存先をログへ記録します。`observe()`の成功直後にファイルが存在する保証はなく、障害時に未保存画像が失われる場合があります。

### 取消しと終了

`observe()`の要求を取り消しても、投入済みの符号化・保存は完了または失敗まで続きます。アプリはサービスとwriterの参照を保持し、`await service.drain()`で進行中の符号化とすべての保存の終了を確認します。待機の取消しでは保存を取り消しません。保存エラーは別途通知するため、`drain()`完了は全画像の保存成功を意味しません。最後の符号化が失敗した場合も、それ以前の保存を回収してからその例外を返します。保存済みの観測は、要求が取り消されても削除しません。

接続を切る場合は新しい観測要求を閉じて`drain()`を待ちます。プロジェクトを選択したままならwriterは保持します。保存先の切替やアプリ終了でプロジェクト自体を閉じるときにだけ、原記録処理の収束後に`writer.close()`を呼びます。

次の関数は、プロジェクト終了時のアプリ所有者が使う例です。呼び出す前に、このwriterを使う新しい要求を止めてください。MCPの各ツール要求から呼ぶ終了処理ではありません。

```python
from gigas.game_io.observation import ObservationError
from gigas.services.observation import ObservationService
from gigas.storage.project import ProjectWriter


async def close_project(service: ObservationService, writer: ProjectWriter) -> None:
    try:
        await service.drain()
    except ObservationError:
        writer.close()
        raise
    else:
        writer.close()
```

`drain()`自体も取り消された場合、保存完了はまだ確認できていません。この例でもwriterを閉じず、呼出側に残した同じサービスで改めて`drain()`を待ってください。保存失敗による`ObservationError`・`ProjectError`が返った場合は処理自体は終了しています。失敗を報告したうえでwriterを閉じられます。実処理の終了前にイベントループを閉じたり、保存先を切り替えたりしないでください。

## 保存形式と読込

プロジェクトには`project.json`、`events.jsonl`、`observations/`、OSロック用の`.gigas.lock`を置きます。管理情報はスキーマ版、プロジェクトID、作成UTC時刻です。ログの共通項目はスキーマ版、イベントID、追記順、記録UTC時刻、イベント種別、プロジェクトID、payloadです。

`observation.recorded`のpayloadには、観測ID、元Frameの取得UTC時刻、画像相対パス、元Frameの世代・連番・幅・高さを記録します。`image`には確定したPNGの`mime_type`・`width`・`height`を記録し、元寸法と区別します。世代・連番は取得サービスの寿命内の補助情報で、観測の永続識別には観測IDを使います。記録時刻と画像取得時刻は別です。

`analysis`欄は生成しません。Codexの解釈を後から原観測へ埋める欄も設けません。開発途中の旧保存形式の読込・移行には対応しません。

`open()`と`reload()`は管理情報とJSONLを検査します。未知のイベント種別や余剰項目は共通形式が正常なら保持し、連番の欠番も許容します。次の記録は最後のsequenceに1を加えて追記します。過去の画像を全件読み込む検査はせず、過去画像の欠損だけでは新規記録を拒否しません。

未知のスキーマ版、不正なUTF-8・JSON・項目・追記順・画像パス、改行で終わらない末尾行は読込失敗になります。JSONLでは問題行と、その前の正常に読めた行数をエラーに示します。原本の切り詰め、自動修復、不完全なプロジェクトの再初期化は行いません。

## 会話と観測の対応

`writer.record_thread(thread_id)`は、実際のCodex会話IDを`thread.started`行のpayloadへ`runtime: "codex"`とともに記録します。アプリはthread/startからIDを取得した後、最初のturn/start前にこの同期処理をGUI・イベントループ外で完了させます。失敗した場合はターンを投入せず、対応保存の失敗を扱います。

`writer.record(observation, thread_id=thread_id)`は同じIDを`observation.recorded`のpayloadへ付けます。IDは空・空白のみでない文字列として扱い、UUID形式に限定しません。writerは現在の会話やID索引を持ちません。単独Python観測では会話がないため省略できます。会話の開始記録も画像と同じJSONLへ採番して追記し、追記結果が不明なら再読込まで次の追記を止めます。

## 保存失敗と書込所有権

| 結果 | 保存物と次の操作 |
|---|---|
| PNG符号化の失敗 | 観測が失敗します。画像とJSONLは書きません |
| 画像保存の失敗 | 観測成功は変えず、保存失敗を通知します。JSONLへまだ触れていないため、writerは引き続き使用できます |
| JSONLの追記・flush・fsyncの失敗 | 公開済みPNGと原記録を保持し、次の追記を拒否します。明示的な`reload()`で検査が通るまで再開できません |
| 保存完了後の要求取消し | 観測記録は保持します。保存成功を応答配送の成功とは扱いません |

観測エラーの`code`は`DEVICE_UNAVAILABLE`、`BUSY`、`OBSERVATION_ENCODING_ERROR`です。作成・読込・保存の失敗は`ProjectError`、同期処理中の二重書込・再読込・終了はその派生型`ProjectBusyError`です。エラー後の自動再試行はありません。

アプリの原記録writerは、選択プロジェクトにつき一つです。`.gigas.lock`のWindows OSファイルロックにより、同じ規約を使う別writerの取得を拒否します。`.gigas.lock`が存在するだけでは使用中とは判断せず、プロセス終了後は再度開けます。利用中にロックファイルを削除したり、アプリ管理の`project.json`・`events.jsonl`・`observations/`を別経路から変更したりしないでください。ネットワーク共有と外部編集の競合解決には対応していません。

このロックは、画像・ログの読取や別ファイルの`memory/`編集を禁止するものではありません。後続の対話記録も同じアプリ所有writerを使い、MCPや通知処理がそれぞれ別writerを開く構成にはしません。現在のCodex接続はread-onlyのため、Codex自身による`memory/`書込権限は別途確認が必要です。

画像は同じディレクトリの一時ファイルへ書き込み、flush・fsync後、既存画像を上書きせず確定します。その後にJSONLへ追記・flush・fsyncします。電源断時にも画像とログが必ず一括で更新される保証はありません。
