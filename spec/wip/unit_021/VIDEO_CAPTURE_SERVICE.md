# U03 M1 継続取得と最新フレーム共有

## 1. 目的・状態・起点

状態: **wip（設計済み・TDD着手）**。[U03](../unit_008/VIDEO_CAPTURE.md)の取得基盤を、[M0](../../complete/unit_020/VIDEO_CAPTURE_DESIGN.md)の調査から実装する。利用者が保存された機器パスを選んで接続し、一つの取得元から最新フレームと取得状態を読めることを目的とする。

## 2. 対象範囲・対象外

UI非依存の機器列挙、設定値からの機器解決、取得プロセス、監視と終了、最新1枚の共有が対象。GUI・設定編集との結合は次の子、保存・MCPはU04、入力はU05。

## 3. 前提・関連文書

[03](../../initial/03-game-io.md)、[02](../../initial/02-runtime.md)、[07](../../initial/07-verification.md)、M0を正本とする。OpenCV 4.14.0.94、NumPy 2.5.4、列挙1.4.0のWindows実列挙と物理機器からのBGR取得は確認済み。入力信号は黒・青のため実ゲームの画質確認は残る。

## 4. 振る舞い仕様・設計

- Windows DirectShowのみを公開する。`DeviceInfo`は名前と機器パスを持ち、空パスの仮想機器は選択対象外。保存パスを接続直前に完全一致で列挙し、不在・重複・旧name/index文字列を別機器へ置換しない。
- `CaptureOptions`は機器パス、width=1920、height=1080、fps=60を持つ。width/heightは16〜3840 / 16〜2160、fpsは1〜120の正整数。既定FourCCのまま要求解像度・FPSを設定し、実画像寸法の不一致はエラーにする。機器のFPS報告値は実測値と別に返す。
- 列挙・open/read/releaseをspawnした子が所有し、親の監視処理から隔離する。起動・列挙10秒、フレーム停止3秒、通常終了2秒、強制終了確認2秒を上限として監視する。停止要求直後に画像を失効させ、正常解放と強制終了、終了未確認を区別する。
- 共有バッファは要求寸法のBGR uint8一枚分。書込み前に型と寸法を検証し、短いロック区間で連番・UTC・単調時計・画像をまとめて更新する。親のロック取得は期限付きとし、異常死した世代の共有資源を再利用しない。
- `Frame`は変更不能なBGR bytes、幅・高さ、接続世代、取得成功時だけ増加する連番、UTC・単調時計を持つ。読み手が遅くても最新1枚だけを保持する。時計はホストでの取得完了を示し、機器の映像時刻ではない。
- 取得成功は最初の有効フレーム到着で確定する。read失敗・例外・期限切れ・切断時は画像を無効化する。再接続は前の子の終了確認後だけ受け付け、連番の所属世代を変える。
- UI側が長時間待たずに開始・停止要求と状態参照を行えるAPIを提供する。監視スレッドはOpenCVを直接呼ばず、機器API固着中も停止を処理できる。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | C1 設定値を検査し機器パスで一意に選択する | new | unit | 同名・空ID・不在・重複・無効モード |
| refactor-skipped | C2 最新1枚の画像・連番・時計を加工から保護する | new | unit | 受信停止と再接続世代 |
| refactor-skipped | C3 DirectShow取得が要求を適用し実フレーム検証と解放を行う | new | unit | 読込失敗・寸法不一致・例外 |
| refactor-skipped | C4 spawn取得・列挙と正常停止を監視する | new | integration | 模擬機器境界を子で使用、実IPCと終了確認 |
| refactor-skipped | C5 open/read/release固着・異常終了・ロック保持中死亡から有限終了する | edge | integration | 古い画像の失効、再接続、強制終了を区別 |

一度に一項目だけをred→greenへ進め、各項目の検証と仕様状態を一つの論理コミットへまとめる。

## 6. 対象ファイル

`src/gigas/game_io/`、対応するunit/integration試験、`pyproject.toml`と`uv.lock`、公開API説明、本書。GUIとstorageの変更は含めない。

## 7. 検証

C1: `uv run --no-sync pytest tests/unit/test_capture_models.py --basetemp=tmp/pytest-capture -q` は未実装importによるred後、16 passed。担当範囲のruff check/formatとty checkはpass。既存構造を変更する必要はなくrefactor-skipped。依存・配布はC3で検証する。実機での製品経路と画質・表示は後続GUI結合で確認する。

C2: `uv run --no-sync pytest tests/unit/test_capture_frames.py tests/unit/test_capture_models.py --basetemp=tmp/pytest-capture -q` は未実装importによるred後、21 passed。最新1枚・加工分離・時計保持・切断と旧世代の拒否を確認。担当ruff/tyはpass。責務はFrameと共有slotに分離済みのため追加refactorなし。

C3: `uv run --no-sync pytest tests/unit/test_capture_backend.py tests/unit/test_capture_frames.py tests/unit/test_capture_models.py --basetemp=tmp/pytest-capture -q` はbackend未実装importのred後、30 passed。DirectShow明示・pathなし除外・要求モード・BGR検査・停止中read結果の破棄・例外時releaseを模擬境界で確認。担当ruff/ty pass、`uv lock --check` pass、`uv build`でwheel/sdist生成成功。依存はOpenCV-headless 4.14.0.94・NumPy 2.5.4・列挙1.4.0を固定。型境界はCamera/CaptureBackend Protocol、画像境界はobjectからNumPy実値を検査し、ignore/castなし。追加refactor不要。実機openは実行していない。

C4: `uv run --no-sync pytest tests/unit/test_capture_models.py tests/unit/test_capture_frames.py tests/unit/test_capture_backend.py tests/integration/test_capture_service.py --basetemp=tmp/pytest-capture -q` はcaptureサービス未実装importのred後、33 passed。実spawn・共有メモリ・Pipeで列挙、画像、切断直後の失効、正常release、再接続世代、終了を確認。Windowsの共有メモリattachでページ丸めがあるため有効画像範囲だけをコピーし、Pipe.pollの閉鎖例外を正常な停止として処理した。担当ruff/ty pass。試験peerを通常pytest・spawn・型検査の全経路でimportするためtests/integration/peersをpackage化し、tyのsource rootへrepo rootを追加。検査対象src/testsは維持。native APIは子、監視と共有slotは親と責務分離済みで追加refactorなし。

配布物の追加検証: root担当で実wheelから機器を開かないspawn列挙・不在pathの拒否・終了を確認する予定。C1〜C5のTDDとは別の配布gateとして結果を追記する。

C5: `uv run --no-sync pytest tests/unit/test_capture_models.py tests/unit/test_capture_frames.py tests/unit/test_capture_backend.py tests/integration/test_capture_failures.py tests/integration/test_capture_service.py --basetemp=tmp/pytest-capture -q` は起動期限が未実装で列挙中のままとなるred後、51 passed（単体30・実spawn結合21）。短いstartup期限の試験は子の起動期限だけを検証し、各機器呼出固着の試験では子が列挙/open/read/releaseへ到達したmarkerを確認してから停止した。read停止期限、read失敗、異常死、共有Lock保持中の死亡、force後の新世代接続、過大制御通知、列挙結果後の終了固着、spawnとLock生成失敗、画像/通知監視例外、join/親close失敗、停止Pipeのclose失敗後のforce回収を確認した。終了未確認ではサービスAPIも再受付を拒否し、未回収資源の参照を保持する。

制御通知を最大64 KiBのJSON、列挙を64デバイスに制限し、サイズ上限付き受信へ変更した。フレームは共有メモリに限定する。C5の担当ruff/format/tyはpass、ignore/castは追加なし。期限、最新画像、終了回収を別メソッドへ分けた実装を確認し、green後の追加構造変更は不要と判断した。実機の映像取得・プレビュー・機器再接続はroot担当で最終版を固定後に確認する。

## 8. 先送り事項

保存・MCPと新フレーム待ちはU04、入力後の実映像遅延はU05。DirectShowの列挙とindexによるopenは原子的でないため、ちょうど機器構成が変わる競合を完全排除できない。ドライバ内部のバッファ遅延と物理抜去は実行した条件だけを記録する。

## 9. 完了チェックリスト

- [x] M0の採用判断と契約を照合した
- [x] 各TDD項目を検証しコミットした
- [x] 型・docstring・公開docsを整合させた
- [ ] gateと未検証事項を記録した
