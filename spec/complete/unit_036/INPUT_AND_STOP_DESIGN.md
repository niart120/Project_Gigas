# U05 M0 操作・通信・停止の設計

## 1. 目的・状態・関連文書

状態は **complete（M0調査・設計完了）**。設定から接続、有限操作、停止、MCPの操作権、事後観測と記録を定める。内部実装と検証は[M1](../unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)、製品結合と実機受入は[M2](../../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md)が所有する。対象CH552の必要受入と最終gateは完了した。

| 起点 | 内容 | 参照 |
|---|---|---|
| 利用者要求 | NyXPyの実装・テストまで追い、Gigasの既存部品を使ってM0を完了する | 2026-11-12の本作業依頼 |
| 親工程 | 通常ターンの操作・停止・事後画像 | [U05親](../../complete/unit_010/INPUT_AND_STOP.md) |
| 先行成果 | 最新キャッシュの観測、加工済みObservation、非同期保存、実MCP | [U04親](../../complete/unit_009/RECORDED_OBSERVATION.md)、[M2](../../complete/unit_035/MCP_OBSERVATION_INTEGRATION.md) |
| 正本 | 所有、入力、原記録、工程、公開接続、UI | [02](../../initial/02-runtime.md)、[03](../../initial/03-game-io.md)、[04](../../initial/04-agent-memory.md)、[05](../../initial/05-implementation.md)、[08](../../initial/08-sdk-compatibility.md)、[10](../../initial/10-ui-interaction.md) |

利用場面は、利用者が入力機器を選んで接続し、通常メッセージを明示送信すること。Codexの操作列を一つずつ実行し、解除後の画像を返す。設定モーダル、保存待ち、質問待ちでも停止できる。U06のGoal状態やU07の終了専用制御は作らない。

## 2. 調査範囲と証拠の区分

コード調査の対象はGigas `e722e42c86c469ce4b6036d92434aeb84148eb4e` とNyXPy `648f860e5afd582f80aa58645570a16bfc68a393`。

以下の「コード確認」は上記commitのファイルとテストの読取、「設計」はGigasの採用契約、「接続確認」は実際に実行したプローブを意味する。NyXのテストの存在を実行成功や実機成功としない。現在の製品実装・検証状態はM1/M2子仕様を参照する。

### 2.1 NyXPyの設定から送信まで

参照rootは `E:/documents/VSCodeWorkspace/Project_NyX`。本節の `gui/`・`framework/` で始まるパスはその `src/nyxpy/` 基準、`src/`・`tests/`・`spec/` で始まるパスはrepo基準。実装・テストの参照は上記commitを基準とする。

| 経路 | コード確認 | Gigasへの判断 |
|---|---|---|
| UI | `src/nyxpy/gui/dialogs/settings/device_tab.py:186-238,316-320,349-358,435-442`。deviceの表示とidentifierを分け、protocol変更で既定baudへ戻す | 表示と値の分離、protocol別既定値を採用 |
| 適用・保存 | `gui/dialogs/app_settings_dialog.py:62-87`、`framework/core/settings/global_settings.py:208-230`。項目ごとのsetが即保存 | Gigasの一つのSettings snapshot保存を維持。NyXの項目別保存へ置換しない |
| 接続用設定 | `framework/core/io/controller_config.py:29-55`、`gui/app_services.py:334-365`。空deviceをNoneへ解決し、protocol/baudを渡す | 空ポートと具体的な接続設定を分ける |
| 構築・対象解決 | `framework/core/runtime/builder.py:325-334`、`runtime/device_selection.py:62-73,119-168`。identifier一致、条件によりdummyへ代替 | 完全一致だけを採用。dummy・名前による代替は不採用 |
| open・送信 | `framework/core/io/device_factories.py:56-104`、`io/adapters.py:25-44,89-90`、`hardware/serial_comm.py:35-48`。factoryが接続を保持、adapterがprotocol bytesを送る | 接続所有者を一つにし、encoderとserial送信を分離 |

NyXの `ProtocolFactory.resolve_baudrate()` はprotocol別集合を検査するが、GUIからruntimeへの上記経路では呼ばれない。UIのbaud候補も全protocol共通であり、protocol変更時だけ既定値を適用する。Gigasでは一つの静的なprotocol定義をUI候補・TOML検証・接続解決に使い、この差を持ち込まない。

NyXの実行中設定変更は `gui/app_services.py:204-280` で反映を延期してruntimeを再構築する。GigasではOK／適用の保存成功後に、旧操作の停止・解除・closeを経て新設定へ切り替える。NyXの実行終了までの延期、runtime全体の再構築、device cacheは移植しない。

### 2.2 NyXPyの通信と停止に関する事実

`hardware/protocol.py` のCH552（122-243行）、PokeCon（246-386行）、3DS（389-595行）はいずれも状態フレームで、押下時間を送らない。時間は `macro/command.py:390-409,438-442` のPC側waitで管理する。`utils/cancellation.py:69-88` は単調時計とEventで中断可能にするが、press内のwaitで例外になると後続releaseへ到達するfinallyはない。`runtime/runtime.py:191-203` はport.closeを呼ぶだけで、serial adapterのcloseはpass。GUI manual解除時の `gui/main_window.py:1661-1676` にはrelease→closeがある。これを全停止経路の解除保証とは扱わない。

`SerialComm` は `serial.Serial(port, baudrate, timeout=1)` とwriteのみ。write_timeout、戻りbyte数検査、ACK受信、再送はない。ファームの通信断時の挙動・neutralの実受信は今回未確認。8N1等のpySerial既定値は[公式API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)で照合したが、Gigasでは明示指定する。

読んだテストは `tests/gui/test_device_settings_tab.py`、`tests/unit/framework/io/test_controller_config.py`・`test_device_factories.py`・`test_adapters.py`、`tests/unit/hardware/test_serial_mock.py`、`tests/unit/protocol/test_ch552_protocol.py`・`test_3ds_protocol.py`・`test_switch_protocol_button_capabilities.py`、`tests/integration/test_3ds_runtime_serial_protocol.py`。byte列、設定、fake serialの経路を検査している。`test_adapters.py` の逐次呼出し試験は並行排他の証拠ではない。PokeConの通常状態フレーム専用試験は見つからず、Gigasでは不足する代表ベクトルを補う。NyXのhardware試験は送信を行うが、ゲーム機側結果のassertionはなく、今回は未実行。

### 2.3 調査時点のGigasと再利用方針

| 現在の事実 | 所有元と差分 |
|---|---|
| 設定はimmutable snapshot、TOMLのコメント・無関係項目を保持、一時ファイル置換 | `src/gigas/storage/settings.py:18-190`。serial_portだけがあり、通信方式・速度・接続は未実装 |
| UIの適用は保存成功後にbaselineを更新。取消しは未適用編集を破棄、OKは保存後閉じる | `ui/settings_dialog.py:257-307`、`app/shell.py:337-436`。接続失敗と保存失敗を分ける既存方式を使う |
| observeはsnapshot一回、符号化だけBUSY、保存は非同期 | `services/observation.py:76-103`。操作後条件をここへ無条件追加しない |
| Frameのmonotonic_nsはread完了直後 | `game_io/capture/backend.py:239-242`。read開始は記録されていない |
| writerの同時利用は待たずに拒否される | `storage/project.py:243-251`。観測と操作の記録を同じ窓口で順序化する |
| MCPは接続ごとのloopback HTTP/Bearer、stopはCodex中断だけ | `mcp/observation.py:47-54,168-182`、`runtime/codex/connection.py:451-470`。旧act呼出しの失効は新規契約 |

## 3. 採用する通信方式・入力表現

### 3.1 対応範囲

U05はWindowsのシリアル入力を対象にし、次の3つのencoderを採用する。機器名の自動判定はしない。対応を宣言する単位は下表の**ファームの通信形式**であり、同じチップや「3DS」と呼ばれる全製品ではない。

| 保存キー / UI表示 | 対象と出力形式 | 既定baud / 選べるbaud |
|---|---|---|
| `ch552` / CH552 | CH552向け11 byte。`AB buttons_lo buttons_hi hat lx ly rx ry 00 00 00` | 9600 / 9600 |
| `pokecon` / PokeCon | PokeCon用Arduinoファーム。`0X<mask> <hat> <lx> <ly> <rx> <ry>\r\n`、大文字16進ASCII。maskは`3 | (buttons << 2)` | 9600 / 9600, 19200, 38400, 57600, 115200 |
| `3ds` / 3DS S2/T3 | New Firmware StickPad DAC対応。14 byteの`A1`ボタン、`A2`slide pad、`A4`C-stick、`B2`touch | 115200 / 9600, 19200, 57600, 115200 |

速度集合はNyX `hardware/protocol_factory.py:30-50`を根拠とする設計値で、全機器で実測済みではない。相手機器の設定速度に利用者が合わせる。3DS旧FT DSの6 byte形式と旧DAC互換は対象外（NyX `spec/agent/complete/local_001/NINTENDO_3DS_SERIAL_PROTOCOL.md`、`local_009/NINTENDO_3DS_STICKPAD_NEW_FIRMWARE.md`）。キーボード、3DSのPOWER・sleep・calibrationコマンド、期限なしhold、手動virtual controller、BluetoothはU05に含めない。

### 3.2 共通の有限入力

MCPは `sequence` だけを受け取る。ポート、baud、raw bytes、権限token、Pythonコードを受け取らない。`press`は完全な状態を指定時間だけ保持し、終了時に全neutralを送る。解除送出に失敗した場合は操作失敗として扱う。`wait`はneutralで待つ。前要素から状態を引き継がない。

```json
{"sequence":[
  {"kind":"press","buttons":["A"],"duration_ms":100},
  {"kind":"press","buttons":[],"left_stick":{"x":100,"y":0},"duration_ms":300},
  {"kind":"wait","duration_ms":250}
]}
```

`buttons`は重複なしの名前配列。方向は`UP/RIGHT/DOWN/LEFT`、隣接2方向は斜め、上下・左右の同時指定は拒否する。stickは省略時中心、xは右が正、yは下が正、各軸は整数-100〜100。`touch`は省略またはnullで未押下、3DSだけ `{x:0..319,y:0..239}` を許す。touchとボタン・stickの同時指定は一つの状態として送る。入力のないpressは拒否しwaitを使う。durationや座標へのbool・小数・文字列、未知キー、非対応入力を黙って丸めたり無視したりしない。

| 能力 | CH552 / PokeCon | 3DS S2/T3 |
|---|---|---|
| 共通ボタン | A/B/X/Y/L/R/ZL/ZR/HOME | 同左 |
| 機器固有名 | PLUS/MINUS/LS/RS/CAPTURE | START/SELECT |
| 方向・左右stick | hat＋左右stick | 十字キーbit＋slide pad/C-stick |
| touch | 拒否 | 320×240座標。キャプチャー画像の座標とは独立 |

3DSのSTART/SELECTを内部でNyXのPLUS/MINUS相当bitへ変換する。モデル向けには機器固有の正式名を使い、別機器のボタン名を暗黙変換しない。全protocolでSL/SRは未対応。対応入力は設定検証と共通のprotocol定義で管理し、6.3節のツール説明にも使う。

共通軸から8 bit状態への量子化は、v<0なら`128 - ((-v * 128 + 50) // 100)`、v>=0なら`128 + ((v * 127 + 50) // 100)`とする。CH552/PokeConはその値を送る。3DS slideはNyXと同じ区分線形変換（0→255、128→128、255→0、Python round）、C-stickは127/128→0、他は`clamp(value-128,-128,127) & 255`。encoderは状態を蓄積せず、完全状態から毎回新しいbytesを作る。

解除フレームはCH552が `AB 00 00 08 80 80 80 80 00 00 00`、PokeConが `0X3 8 80 80 80 80\r\n`、3DSが `A1 00 00 A2 80 80 A4 00 00 B2 00 00 00 00`。3DSはtouchも解除する。bit対応はNyXの上記実装を参照し、Gigas側の試験は代表byte列を固定して誤変換を検出する。

### 3.3 通信条件と時間の意味

設計としてpySerial 3.5をM1で追加する。pySerialが扱う各OSのnative portを利用し、アプリ独自のOS拒否は設けない。URL transportやnetwork serialは受け付けない。8 data bits、parity none、1 stop bit、xonxoff/rtscts/dsrdtrはfalse、read timeoutは0（ACK読取なし）、write_timeoutはpySerial既定のNoneとし、完了時間の性能要求は置かない。DTR/RTSはpySerial既定のtrueを明示し、開閉時のresetと起動待ちの要否をM2で対象機器ごとに確認する。OS別の実接続・実機確認は実装対応と区別する。

一つのwriteが全byte数を返した場合だけ「送出済み」とする。部分write・例外は送出結果不明。flushは別の無期限待ちを増やすため通常送信に挟まず、ACK・ゲーム受理・物理解除を成功条件と呼ばない。[pySerial API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)と[Windows実装v3.5](https://github.com/pyserial/pyserial/blob/v3.5/serial/serialwin32.py)のwrite/flush/timeoutを確認した。ドライバー不具合まで期限内に終わる保証ではない。

| 条件・期限 | 採用方針と理由 |
|---|---|
| 一要素 | press/waitとも正の整数ms。入力時間の固定上限は設けない |
| 列 | 1要素以上の有限配列。要素数・指定時間合計の固定上限は設けない。ループ、無限生成、期限なしholdは受け付けない |
| write | 完了時間の性能要求は置かない。pySerialの開始済みwriteを回収してからneutral/closeへ進む。CH552の解除送出はM2で確認。最悪所要時間と解除ACKはU10へ残す |
| MCP | 同期要求の待機期限を300秒とする。内部入力の固定上限とは分け、期限時の停止範囲は6.4節に従う |

pressのdurationは**状態write完了からneutral送信を開始するまでのPC側の待機時間**であり、物理保持時間ではない。waitのdurationはneutral状態での待機時間を表す。長い待機も途中停止できるようにする。ゲームが認識できる最短時間は実機で確認する。

pySerialのwriteは既定ではblockingである。取消し後も開始済みwriteが戻るまで回収し、その後にneutral/closeへ進む。ソフトウェア上の停止受付と機器の解除完了は区別する。

## 4. 設定UIから接続まで

### 4.1 保存と解決

既存のI/Oタブ・コントローラー群にポート、プロトコル、通信速度を置く。ポートは非編集comboで「未接続」（空文字）、列挙した `COM3` 等のidentifierと説明を表示する。列挙でportを開かない。見つからない保存値もその値のまま残して表示し、自動選択しない。protocol既定はch552、port既定は空。baudはTOMLで省略可能な整数で、省略時だけ選択protocolの既定へ解決する。Settingsのserial_baudrateはintまたはNoneとし、未指定を0で表さない。

```toml
[controller.serial]
port = "COM3"
protocol = "ch552"
baudrate = 9600
```

現在の `controller.serial.port` / `Settings.serial_port` を維持し、serial_protocolとserial_baudrateを追加する。未指定protocolはch552。protocolとbaudの検証は設定の共通処理に集約し、不正な値を拒否する。省略baudはUIで実効値を表示し、編集snapshotでは具体値にする。protocolを変更したときは候補を入れ替えてそのprotocol既定を選ぶ。再表示では保存値を勝手に既定へ戻さない。

baud未指定はTOMLのキー省略で表す。接続対象は保存したport identifierとの完全一致で選ぶ。

```text
設定UIの下書き
  → Settings snapshot（共通の設定検証）
  → 既存SettingsIO / save_settings（1回のTOML置換）
  → 保存成功後baseline更新
  → OK／適用に対応した入力接続設定の反映（空ポートなら停止・切断）
  → 最新ポート一覧とidentifier完全一致、baud既定値解決
  → 接続用の値（port, protocol, 実効baud）
  → 入力サービス → serial adapter → encoder / write
```

接続用値はサービスループへ一つのsnapshotとして渡し、workerからSettingsDialogやTOMLを読み直さない。portの存在検査は接続直前に行い、保存が成功しただけでは機器が有効とは表示しない。

### 4.2 適用・取消し・接続中の変更

| 操作・状態 | 契約 |
|---|---|
| 適用 / OK | 全snapshotを検証・保存し、成功後にbaselineと接続先へ反映。変更がなく保存済みなら再保存は不要。OKは保存成功後に閉じ、接続完了を待たない。適用は画面を残す |
| 取消し / Escape / × | 未適用編集だけを破棄。提出済みの保存・適用は取り消さず、適用済みの設定・接続へ巻き戻さない |
| 保存失敗 | 下書き・旧baseline・旧接続を保持し、接続変更を開始しない。既存通知詳細へ理由を残す。停止actionは独立して使える |
| 接続中・操作中の変更 | 入力項目は編集可能。保存成功後に旧Grant失効・残り列破棄・neutral・closeを進め、旧worker収束後だけ新設定をopenする。旧操作は再開しない |
| 「未接続」の適用 | 空portを保存し、同じ停止・解除の後にcloseする。protocol/baudは保持する。未開始の接続予定も取り消す |
| 接続失敗・再適用 | 保存値は選択先のまま保持し、接続失敗を既存通知詳細へ残す。旧接続へ自動復帰しない。利用者のOK／適用で同じ設定を再試行できるが、旧worker未収束なら拒否する |

入力の独立した接続・切断ボタンやメニューは設けない。映像の既存 `ApplicationShell._apply_saved` と `VideoController.apply_capture` と同様、設定保存と機器への適用結果を分ける。非空portではopenとneutral送出の成功で接続済みとするが、ゲーム側解除確認とはしない。起動時は保存値を表示するだけで入力portを自動openしない。接続だけでは操作権を発行しない。

比較には正規化したport/protocol/実効baudを使い、同一条件で接続済みなら開き直さず、実行中の列も止めない。baud省略から既定値への表現変更だけでは切替としない。入力項目を変更した適用、または設定全体が未変更の明示的なOK／適用を入力への適用対象とする。他項目だけの変更では入力の再接続を始めない。同様に入力だけの変更で映像取得やCodexを再接続しないよう、現在の `ApplicationShell.apply_settings` の変更領域判定を改訂する。

設定の適用は既存の映像設定と同じ流れを使う。保存中は既存SettingsIOの一件受付に従い、接続切替中の再適用では未開始の接続先を最新の保存値へ置き換える。旧接続を閉じてから新接続を開く。

停止は保存・切替中も受け付け、未開始の接続も取り消す。停止前に受け付けた適用が後から完了しても接続を開始しない。再接続には新しいOK／適用を必要とする。既存の適用処理と共通停止を使う。

保存完了までは旧接続・旧操作を維持する。保存成功後の接続変更に失敗した場合は保存値を保持し、接続状態と理由を既存通知へ表示する。自動的な巻戻し・再試行は行わない。

GUIにはラベル・値・短い状態とエラーだけを置く。将来のBluetooth選択肢、未実装機器、説明文を先行表示しない。

## 5. 接続・操作・停止の所有

### 5.1 共通部分とシリアル部分

既存サービススレッドのasyncioループが入力サービスを一つ所有する。共通の実行部は要求受付、有限列の実行と取消し、解除、事後観測を担当する。serial adapterだけがport、baud、8N1、bytes、進行中writeの扱いを知る。同期open/write/closeは専用の単一workerで直列に実行し、一度に一つだけ投入する。GUIとMCPが直接writeしたり、停止用に第2portを開いたりしない。

共通の実行部は入力状態と送出結果を扱い、port・baud・byte変換はserial adapterへ閉じ込める。Bluetoothは同じ操作・停止契約を使う後続対応とし、現在の設定や実装へ追加しない。

接続状態と操作の実行状態は分ける。接続の開閉は入力サービスが所有し、終了するまで次の接続を開かない。接続はチャット・記録先の作成を条件とせず、観測・記録サービスはrun開始時にGrantへ固定する。停止と障害時の扱いは5.3節に従う。

### 5.2 列の実行

入力サービスが一つの操作列を最後まで担当する。要求の受付時に操作権・入力接続と列全体の内容を確認する。別の列を実行中ならBUSYを返し、待ち列は作らない。

実行順序は次のとおり。

1. 操作開始記録を非同期投入する。保存完了を待たない。
2. 列を順番に実行する。pressは状態送信→指定時間待機→neutral、waitはneutralで指定時間待機する。
3. 最後の解除・待機完了後のFrameを選び、U04のObservation生成へ渡す。
4. 結果記録と画像の非同期保存を依頼し、結果を返す。

入力枠は列と解除の終了時に解放し、直後に最新Frameを選ぶ。符号化中は次の入力を許可する。停止は観測応答を取り消し、開始済みの符号化・保存は観測側で継続する。停止時に観測IDの完成を待たず、後から操作記録へ追記しない。

要素間に暗黙の待ち時間は加えない。同じボタンを別のtapとして認識させたい場合は、列にwaitを明示する。

### 5.3 停止・解除・切断・終了

停止処理は入力サービスに集約する。新しい要求を受け付けないよう操作権を失効させ、実行中の操作を取り消す。実行部は終了処理でneutralを送り、残りの列を破棄する。Codexへの中断要求も行うが、その応答や記録・画像保存を待ってから入力を止める順序にはしない。

| 入口 | 扱い |
|---|---|
| GUI中断、run終了、Codex切断・通信失敗 | 共通停止を呼び、入力と観測応答を取り消す。正常な入力接続は維持する |
| act要求取消し・待機期限 | 入力実行中なら共通停止。観測中ならその応答だけを取り消し、後続入力と操作権を維持する |
| 入力設定変更・「未接続」の適用 | 保存成功後に停止し、旧接続を閉じる。その後の接続は4.2節に従う |
| 映像停止・切替・取得障害、記録失敗 | 入力は停止しない。観測失敗と保存失敗を個別に通知する |
| アプリ終了 | 終了確認より先に停止し、終了確定後に接続と保存処理を閉じる。終了を取り消しても旧操作を再開しない |

設定・通知・確認モーダルからも同じ停止を使えるようにする。解除だけの独立GUIやMCPツールは設けない。U06のpause/stopもこの停止処理を使う。

質問・承認待ちは一時保留として、新規入力を受け付けず実行中の列を取り消す。既存の対話管理で質問・承認待ちが解消したら、停止されていない同じrunの新しい要求を受け付ける。取り消した列を途中から再開しない。既存の質問・承認管理を使い、入力側に別の要求管理を作らない。

正常完了・通常の停止・Codex切断では入力接続を維持する。入力設定切替・「未接続」の適用・アプリ終了では実行部の終了後に接続を閉じる。送信・解除に失敗した場合は操作を失敗として記録し、接続を閉じて利用者の再適用を待つ。送出済みと実機での解除確認は区別する。解除結果は `not_needed`（入力前中断などで解除不要）、`sent`（解除送出済み）、`unconfirmed`（解除送出未確認）とし、未確認を成功としない。自動再送・自動再接続はしない。

進行中の同期writeは3.3節の方針で回収する。未終了のI/Oを残して別の接続を開かない。取消し・終了処理には既存の非同期処理を使う。

## 6. MCP・操作権

### 6.1 採用版の公開経路で確認したこと

対象は `openai-codex-cli-bin==0.167.0` とMCP SDK 2.3.0。[公式App Server資料](https://learn.chatgpt.com/docs/app-server)、採用binaryの生成schema、採用tag `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a` の設定schemaとtool呼出し処理、実socketプローブを根拠とする。

| 確認した事実 | 設計への適用 |
|---|---|
| thread/start.configから新MCPを公開し、模擬actを呼び出せる | 初回は既存thread/startを使う |
| loaded状態のresumeだけではMCP設定が更新されない。unsubscribe後の同ID resumeでは新MCPを使え、既存2turnを保持できる | 実行間のMCP更新はunsubscribe→resumeで行う |
| 失効したGrantの模擬呼出しは入力0回で拒否され、旧Bearerで新endpointへ接続すると401になる | runごとに操作権とendpointを結び付ける |
| rollout未作成のthreadはresumeできない。存在しない旧serverのenabled=falseだけを含む設定はinvalid transportとなる | resume失敗を通知し、新configには新serverの完全設定を渡す |

実モデルによる短い模擬操作2turnも成立した。検証結果は9節に集約する。これは公開経路の確認であり、製品の取消し処理・長時間操作・実機入力の確認ではない。

### 6.2 権限の所有と更新

GameIoMcpServer(observations, inputs, protocol)を通常送信ごとに作り、一つのrunの固定Grantをbind_grantで一度だけ対応させる。MCP境界はそのGrantを内部要求へ渡し、入力サービスの要求受付で判定する。モデル引数や「現在のGrant」への読み替えで権限を与えない。受付済みの操作は入力サービスが所有し、停止時に取り消す。

操作権は入力接続のある通常turnに対して有効にし、停止・turn終端・開始失敗で失効する。入力未接続で開始したrunは入力不可のままとする。接続変更後は次の明示送信から操作できる。質問・承認待ちは5.3節の一時保留を使う。

プロトコル切替はOK／適用で旧操作を止め、入力接続へ反映する。各通常送信のprepare_turn(prompt, thread_id)では旧操作とturnの終了後に旧endpointを閉じ、適用中の接続に合うツール説明で新endpointを公開する。既存threadは `thread/unsubscribe` の完了後、同IDの `thread/resume` に新configを渡し、Codex側にも新しいMCP設定・ツール定義を読み込ませてからturnを開始する。初回はthread_id=Noneで準備し、thread/startを使う。更新に失敗したら送信失敗として原稿と記録先を保持し、既存のCodex切断・接続からやり直せるようにする。新規会話への自動置換や旧操作権の復活は行わない。

Grantはact専用であり、HTTP認証はU04の仕組みを使う。入力不可・一時保留・Grant失効でも、開いているendpointのobserveは利用できる。入力拒否はINPUT_DISABLEDを返す。

U06のGoal再開への影響は同工程で確認する。

### 6.3 ツールの公開と承認

act_and_observeの説明には、実際の入力接続へ適用中のプロトコル、対応ボタン・スティック、タッチ対応の有無を含める。3DSのタッチは座標範囲（x:0〜319、y:0〜239）と観測画像の座標とは別であることを示す。未接続なら入力不可と明記する。機器の能力情報はこのツール説明に集約し、独立した変更通知・状態取得ツールは設けない。

公開はobserveとact_and_observeだけ。後者の要求検査・内部状態への変換はMCP境界、byte変換はserial encoder、停止と実行は入力サービスに置く。正常結果はU04と同じimage＋JSON textにoperation_idを加え、画像やIDを二重生成しない。エラーはisError=true、code・phase・operation_id（作成済みの場合）・完了要素数・neutral結果を返す。入力前拒否では入力0回を明示し、事後画像失敗に同じ意味を付けない。

actはゲーム状態を変えるので `readOnlyHint=false, destructiveHint=true, idempotentHint=false, openWorldHint=true` とする。U04のobserveに付けた非破壊・閉じた対象の注釈を流用しない。採用版の公開設定 `mcp_servers.<固有名>.tools.act_and_observe.approval_mode = "approve"` を、その実行用のアプリMCPだけへ設定する。利用者の明示送信、選択機器、有限列、Grantに限定した許可であり、globalのapprovalPolicy=on-request / approvalsReviewer=user、他ツールの承認は変更しない。

per-tool設定は採用版schema・ソース、実モデルの模擬入力、M2の製品GUI・CH552実操作で確認済み。

### 6.4 長い有限操作と要求の寿命

内部操作は列の終了、停止、権限失効、明示的な通信失敗・取消しで終了する。入力時間・要素数・合計時間へ固定上限は設けない。

公開は同期MCPを使い、Codexのtool_timeout_secとGigas側のact_and_observeの待機期限を300秒とする。数秒〜1分中心の用途に対する設計判断であり、MCP共通の最大値ではない。dynamic tool、非同期タスク受付・ポーリング、暗黙の入力分割は採用しない。

期限切れは入力実行中なら共通停止、観測中ならその応答だけの取消しとする。後者では後続入力と操作権を維持し、開始済み符号化・保存を独立して回収する。期限エラーを返せる場合も入力結果と解除完了は未確定として扱う。両端の計時は別であり、期限内の結果配送・物理解除は保証しない。自動再送せず観測で状態を確認する。HTTP切断やcancel通知に停止を委ねず、GUIは入力所有者へ直接停止を届ける。詳細と検証は[M2子仕様](../../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md)が所有する。

## 7. 操作後の観測・記録

### 7.1 事後Frameの確定

操作列の最後の解除・末尾waitが完了した後に、単独observeと同じ処理で最新キャッシュを一度取得する。新しいFrameを待たず、操作前や前回観測と同じFrameも許容する。画像取得時刻やゲームへの入力反映は保証しない。

取得状態と画像なし、符号化競合・失敗はobserveと同じ扱いとする。入力済みの可能性と完了要素数を残し、入力を自動再実行しない。入力の受付・継続は映像と保存の成功を条件にしない。観測・保存の失敗は操作権を失効させず、それぞれのエラーとして返す・通知する。

生成したPNGを応答と非同期保存で共用する。入力枠は列と解除の終了時に解放し、直後に最新Frameを選ぶ。符号化中は次の入力を許可する。停止は観測応答を取り消し、開始済みの符号化・保存は観測側で継続する。停止時に観測IDの完成を待たず、後から操作記録へ追記しない。

### 7.2 記録とID

通常の記録は「何を入力し、どこまで実行され、どの観測が得られたか」を残す。port・baud・protocolなどの接続情報はrun・操作イベントへ含めない。通信障害の調査に必要な接続情報は、既存の診断ログで扱う。対応入力をモデルへ伝えるツール説明は6.3節の責務とする。

U05では通常送信一回をrequest_id、そこから停止・終端までをrun_id、ツール呼出し一回をoperation_idで識別する。IDはアプリが発行する。Goalのtask_idは作らずU06へ残す。thread_idは採用runtimeの実ID、turn_idは確認できた時点から付け、未確認の仮IDを作らない。

既存JSONL共通形式を使い、次の意味だけを追加する。詳細型とTDDはM1で所有するが、開始記録の順序と必須の対応は本M0で固定する。

| イベント | 必須の対応・内容 | 保存と実行の順序 |
|---|---|---|
| `input.run_started` | request_id/run_id、実thread_id、利用者の通常依頼本文、UTC | thread対応の後に非同期投入。保存成功を操作権の前提にしない |
| `input.operation_started` | request_id/run_id/operation_id、受け付けた入力列（ボタン・スティック・タッチと各要素の指定時間）、確認済みならturn_id | 全体検査・枠確保後、最初の状態送信前に非同期投入 |
| `input.operation_finished` | operation_id/run_id、実行結果（completed/interrupted/failed）、完了要素数、確定時だけobservation_id。中断・失敗時は処理段階・理由、入力送出状況、解除結果 | 終了処理で解除結果が判明した後に非同期保存。解除失敗も記録し、再送しない |
| `input.run_finished` | request_id/run_id、終端理由、確認できたturn_id、停止/通常終端/開始失敗の区別 | 先に権限を閉じる。操作結果の後にrun終端を記録 |

操作開始行は送信開始の証拠ではなく、受け付けた列の記録である。停止により入力0回の場合もあり、保存失敗で開始行自体が欠ける場合もある。終端が欠ける場合は結果不明として扱い、自動再実行しない。完了要素数はpressのneutral成功またはwait完了までの数で、途中要素が実行されなかったという意味ではない。

観測イベントはU04のまま。操作終端のobservation_idから `observation.recorded` とPNGへ結び付ける。画像保存失敗でも確定済みIDを消さず、PNG未保存・終端記録失敗を別の通知で識別する。Observationへrunや操作状態を重複保持しない。取得世代・連番は取得サービス内の識別情報であり、再起動をまたぐ永続IDにしない。

### 7.3 保存と停止の分離

観測・run・操作の記録は、同じProjectWriterを使う一つの記録窓口で順序化する。U04の非同期保存を拡張し、入力列や画像処理を保存の排他区間へ含めない。Lockの配置やTaskの構成はM1で既存実装に合わせる。

run・操作の開始と終端は同じ記録窓口へ順に非同期投入する。保存完了を入力開始の前提にせず、保存失敗でも入力を止めない。記録が欠けたまま操作される場合を許容し、失敗は診断へ残す。

操作記録のresultは入力列の実行結果を示す。入力完了後の観測失敗・応答取消しでもcompletedを維持し、phase=observeと理由を添える。要求全体のcodeとは区別し、正常に返せた画像だけobservation_idを対応付ける。

通常終了・保存先変更では引受け済み保存を完了してからwriterを閉じる。保存失敗は既存通知にIDと理由を残し、確定済みの操作・観測結果とは分ける。

## 8. 既存仕様の要否とテスト方針

単一入力所有者、全体検査、操作前記録、停止優先、入力列と解除までの排他、旧要求失効、自動再送しない契約は維持する。未使用のGoal状態、終了専用権限、停止専用MCP、NyXのruntime再構築・dummy、複数接続cache、上限のGUI設定は不採用とする。正常な保存失敗を物理操作失敗へ書き換えず、解除sentと実機確認を分ける。

M0は文書整備のためTDDのred/greenは **not applicable**。次の表はM1/M2の受入への引継ぎであり、製品テストの実行結果や未着手の詳細Test Listではない。

| 既存試験 | 改訂・統合する契約 |
|---|---|
| `tests/unit/test_settings.py`、`tests/integration/test_settings_io.py` | protocol/baudの既定・不正組合せ、既存TOML原本保持、snapshotの保存 |
| `test_settings_apply_contract.py`、`test_settings_accept.py`、`test_settings_source_menu.py` | 入力設定だけの適用、取消し・保存失敗、接続中変更の停止・切替、同一条件の再適用、未接続の適用、切替中の最新値反映、ポートの表示と保存値 |
| `test_ui_control_lifetime.py`、`test_codex_shutdown.py`、`test_app_lifetime.py` | モーダル・保存中停止、終了確認より先の停止受付、終了取消しで再許可しない |
| `tests/unit/test_capture_backend.py`・`test_capture_transport.py`・`test_capture_frames.py` | 取得完了時計の搬送、最新キャッシュと既存世代失効 |
| `test_observation_service.py`・`test_observation_storage.py` | キャッシュ観測維持、入力列・実行結果と観測IDの対応、接続情報を含めないイベント、記録の順序化、開始記録中の停止、保存失敗・取消し・drain |
| `test_game_io_mcp.py`・`test_codex_service.py`・`tests/unit/test_codex_connection.py` | 旧tokenでの受付拒否、受付済みactの取消し、新grant、プロトコル切替後の説明とCodex側の再読込、observeの既存寿命、設定配線 |

追加する試験は、3protocolの完全状態→byte列とneutral、固定上限を持たない有限列の全体検査・長い待機の途中停止・同時要求・部分write、事後Frame条件という新しい振る舞いに限る。NyXの内部メソッド単位の試験を一式コピーしない。仮想時計・明示Eventで競合位置を制御し、短いsleepだけで完了を推測しない。UIの文言禁止・見出し・チェック欄だけをassertする試験は追加しない。

## 9. 対象ファイル・検証

対象文書は本子、親unit_010、関連初期設計と参照リンク。製品コード・既存テスト・依存・公開API・利用者向けdocsは変更しない。将来の実装対象は `game_io/input/`、`services/`、`storage/`、`mcp/`、`app/`、`ui/`、`game_io/capture/` と対応試験。serialをcaptureと平置きにしない。

### 9.1 確認済みの範囲

| 検証command | 結果と限界 |
|---|---|
| `uv run --no-sync python -X utf8 tmp/u05-mcp-generation-probe.py` | pass。採用runtimeの公開RPC、新endpoint、旧Grant拒否、旧Bearer拒否、同ID resumeを確認。物理書込なし。結果: `tmp/u05-mcp-generation-bljcvlpg/results.json` |
| `uv run --no-sync python -X utf8 tmp/u05-mcp-model-probe.py` | pass。gpt-6.1-sol/low、同threadの2turnで各1回の模擬press A 100 ms、neutral、固定PNGと観測IDを照合。tool単位approveと全体on-request/userで成立。物理書込なし。結果: `tmp/u05-mcp-model-n0rrwo9a/results.json` |
| `uv run --no-sync python -X utf8 tmp/validate-u05-docs.py` | pass。変更文書のローカルリンク、unit一意性・配置、仮テキスト、空白、コードフェンスを検査 |
| `git diff --check` | pass。差分の空白検査 |
| 本文・親仕様・関連初期設計の照合 | 有限列、OK／適用による接続、受付時の権限判定と取消し、MCP能力説明と再読込、操作中心の記録、M1/M2の確認時期を照合 |
| 製品ruff/ty/pytest/build/lock | not applicable。仕様のみの変更 |
| NyXテスト実行、製品の入力・停止結合、長時間MCP、シリアル・実機・GUI | not run。コード読取と模擬公開経路の成功を製品・実機検証へ読み替えない |

プローブ・生成schema・結果はignored tmpに置く。外部リンク全件・Markdownアンカー・Mermaid描画は文書検査の対象外。通信形式と接続設定、停止の責務、観測・記録の対応は参照コードと本文で照合する。

上記はM0の調査・プローブ実績である。M2では製品GUI・実モデル・CH552の操作と停止、模擬機器による65秒要求・能力更新・短縮期限を確認した。最終gateは全体660件と静的検査・配布検証が成功し、詳細はM2子仕様に置く。

## 10. 後続実装と未確認事項

### 10.1 M1開始時に揃えるもの

M1は現コード・依存版・Git状態と本仕様を照合し、既存試験の改訂先と実装範囲を決める。[M1子仕様](../unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)で内部実装と模擬境界の検証は完了した。

対象はシリアル3形式の設定解決・入力変換、有限列と共通停止、操作記録、事後観測である。BT、Goal、手動コントローラーは含めない。内部境界の模擬検証と、M2の公開経路・実機受入を分ける。

### 10.2 内部実装の境界

| 境界 | 採用契約と検証先 |
|---|---|
| 進行中writeの停止 | 固定期限を設けず、開始済みwriteを回収してからneutral/closeへ進む。模擬試験とCH552の解除送出を確認。最悪所要時間・解除ACKは未検証 |
| 有限列と公開期限 | 内部の時間・要素数上限は設けず、公開は同期300秒とする。採用runtimeで65秒wait・途中停止・短縮期限を確認。300秒経過の試験ではない |
| 取消しと記録・画像生成 | 入力中は共通停止、観測中は当該応答だけ取消し。開始済み保存を回収し、取消した列を再開しない。既存試験と公開経路で確認済み |
| 事後Frameの受渡し | 最新キャッシュを返すobserveを共用し、同じFrameの再利用を内部試験で確認済み |

M1完了には採用した内部実装の試験と未検証範囲の明記を必要とする。公開経路・GUI・実機の結合はM2で受け入れる。

### 10.3 M2の受入結果と残る範囲

| 対象 | 確認結果と限界 |
|---|---|
| 設定とMCPの更新 | OK／適用・取消し・保存失敗・接続変更を自動検証。実GUIのOKで接続し、模擬機器の3DS再読込と旧要求拒否、実CH552の同thread継続を確認 |
| 長時間要求と停止 | 採用runtimeと製品MCPで65秒wait完了、65秒press途中停止、短縮期限のTOOL_TIMEOUTを確認。300秒経過は未検証 |
| GUI・質問・承認・終了 | 実GUIの設定モーダル停止と通常終了を確認。外部の要求解消後の保留解除、回答送信中の保留、準備中の接続変更後に旧runが復活しないことを自動試験で確認 |
| 対象機器と映像 | USB Video DeviceとCOM8・CH552・9600 baud、実モデルで3runを確認。B長押しをGUI停止しneutral sent、別runの左移動後の停止映像と保存を確認。B解除ACK、write最悪時間、DTR/RTS reset、PokeCon・3DS実機、他OS、長時間連続は未検証 |

実機条件・長時間要求の残る確認は[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)、Goal再開はU06、再起動復帰はU08へ引き継ぐ。[Bluetooth事前調査・導入計画](../../planned/unit_037/BLUETOOTH_INPUT.md)は独立したplanned仕様とし、U05の完了条件へ加えない。

## 11. 完了チェックリスト

- [x] NyXの設定から送信・停止とテストを追跡した
- [x] 設定、入力、所有、停止、操作権、事後観測、記録を具体化した
- [x] 採用版の公開経路の確認と未検証範囲を分けた
- [x] 親・恒常仕様・参照先を更新し、M1条件を残した
- [x] 文書を検査し、M0の完了と製品・実機の未検証範囲を区別した
