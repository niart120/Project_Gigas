# 記録付き観測API

Windowsで、取得済み画像をPNGとJSONLへ記録するPython APIです。保存先を選ぶGUIとMCP・Codexへの画像配送は、現在のアプリには接続されていません。

入口は`gigas.storage.project.ProjectWriter`と`gigas.services.observation.ObservationService`です。元画像には[映像取得API](video-api.md)の不変な`Frame`を使います。

## 一枚を保存する

`ProjectWriter.create(path)`は、親ディレクトリが存在し、保存先自体は存在しない場合にだけ作成します。既存プロジェクトには`ProjectWriter.open(path)`を使います。どちらも同期処理なので、GUIスレッドやイベントループ上で直接実行しないでください。

次の例は機器を使わず、16×16の青いBGR画像を一時プロジェクトへ保存します。書込可能な作業ディレクトリで`uv run python example.py`を実行してください。一時ディレクトリは例の終了時に削除されます。

```python
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gigas.game_io.capture.models import Frame
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
        observation = writer.record(frame)
        saved_image = writer.path / observation.image_path
        assert saved_image.read_bytes() == observation.png_bytes
        assert observation.frame is frame
    finally:
        writer.close()
```

`record(frame)`は、元寸法のPNGへの符号化、画像保存、JSONL一行の追記が成功してから`Observation`を返します。`observation_id`は`obs-`にUUID4を続けた値、`image_path`は`observations/<observation_id>.png`です。保存するPNGと返す`png_bytes`は同じbytesです。同じFrameを再度記録しても、新しい観測IDを発行します。

画像の対応範囲は幅16〜3840、高さ16〜2160、符号化後32 MiB以下です。切り抜き・縮小・拡大を行わず、プレビューの黒帯や表示倍率も持ち込みません。`Observation.from_frame(frame)`は符号化だけを行い、プロジェクトへ保存しません。

## 最新画像を非同期で観測する

`ObservationService(reader, writer)`には、既存の`CaptureService.reader()`と開いたwriterを渡します。サービスが機器を開始・再接続することはありません。

`await service.observe()`はsnapshotを一度読み、`phase == "streaming"`で画像がある場合に採用します。呼出しより前に取得した最新キャッシュを使い、新しいフレームを待ちません。採用前の取得停止・エラーでは失敗し、採用後に取得が停止しても、その画像の保存を続けます。`captured_at`は元Frameの時刻を保持します。撮像時刻、機器内部の遅延、操作後の画面であることは保証しません。

符号化・保存は呼出側の既存asyncioループから別スレッドへ渡します。非同期メソッドは同じイベントループから使ってください。同時に一件だけを処理し、保存中の次要求は`ObservationError`の`code == "BUSY"`で拒否します。要求を待ち行列へ入れたり、自動で再実行したりしません。

### 取消しと終了

`observe()`の要求を取り消しても、投入済みの保存処理は完了または失敗まで続きます。呼出側はサービスとwriterの参照を保持し、新しい要求を止めてから`await service.drain()`で処理終了を確認します。その後に`writer.close()`でOSロックを解放します。保存済みの観測は、要求が取り消されても削除しません。

次の関数は、一件の観測を待ち、要求取消しの場合も保存処理を回収してwriterを閉じます。同じwriterを持つサービスを渡してください。サービス生成とwriterの作成・読込は呼出側が担当します。

```python
from gigas.game_io.observation import Observation, ObservationError
from gigas.services.observation import ObservationService
from gigas.storage.project import ProjectError, ProjectWriter


async def observe_then_close(service: ObservationService, writer: ProjectWriter) -> Observation:
    try:
        return await service.observe()
    finally:
        try:
            await service.drain()
        except (ObservationError, ProjectError):
            writer.close()
            raise
        else:
            writer.close()
```

`drain()`自体も取り消された場合、保存完了はまだ確認できていません。この例でもwriterを閉じず、呼出側に残した同じサービスで改めて`drain()`を待ってください。保存失敗による`ObservationError`・`ProjectError`が返った場合は処理自体は終了しています。失敗を報告したうえでwriterを閉じられます。実処理の終了前にイベントループを閉じたり、保存先を切り替えたりしないでください。

## 保存形式と読込

プロジェクトには`project.json`、`events.jsonl`、`observations/`、OSロック用の`.gigas.lock`を置きます。管理情報はスキーマ版、プロジェクトID、作成UTC時刻です。ログの共通項目はスキーマ版、イベントID、追記順、記録UTC時刻、イベント種別、プロジェクトID、payloadです。

`observation.recorded`のpayloadには、観測ID、元Frameの取得UTC時刻、画像相対パス、元Frameの世代・連番・幅・高さ、空の`analysis`を記録します。世代・連番は取得サービスの寿命内の補助情報で、観測の永続識別には観測IDを使います。記録時刻と画像取得時刻は別です。

`open()`と`reload()`は管理情報とJSONLを検査します。未知のイベント種別や余剰項目は共通形式が正常なら保持し、連番の欠番も許容します。次の記録は最後のsequenceに1を加えて追記します。過去の画像を全件読み込む検査はせず、過去画像の欠損だけでは新規記録を拒否しません。

未知のスキーマ版、不正なUTF-8・JSON・項目・追記順・画像パス、改行で終わらない末尾行は読込失敗になります。JSONLでは問題行と、その前の正常に読めた行数をエラーに示します。原本の切り詰め、自動修復、不完全なプロジェクトの再初期化は行いません。

## 保存失敗と書込所有権

| 結果 | 保存物と次の操作 |
|---|---|
| PNG符号化・画像保存の失敗 | 当該観測は失敗します。JSONLへまだ触れていないため、writerは引き続き使用できます |
| JSONLの追記・flush・fsyncの失敗 | 公開済みPNGと原記録を保持し、次の追記を拒否します。明示的な`reload()`で検査が通るまで再開できません |
| 保存完了後の要求取消し | 観測記録は保持します。保存成功を応答配送の成功とは扱いません |

観測エラーの`code`は`DEVICE_UNAVAILABLE`、`BUSY`、`OBSERVATION_ENCODING_ERROR`です。作成・読込・保存の失敗は`ProjectError`、同期処理中の二重書込・再読込・終了はその派生型`ProjectBusyError`です。エラー後の自動再試行はありません。

一つのプロジェクトを同時に開けるwriterは一つです。別プロセスもWindowsのOSファイルロックで拒否します。`.gigas.lock`が存在するだけでは使用中とは判断せず、プロセス終了後は再度開けます。利用中にロックファイルを削除したり、プロジェクトを外部編集したりしないでください。ネットワーク共有と外部編集の競合解決には対応していません。

画像は同じディレクトリの一時ファイルへ書き込み、flush・fsync後、既存画像を上書きせず確定します。その後にJSONLへ追記・flush・fsyncします。電源断時にも画像とログが必ず一括で更新される保証はありません。
