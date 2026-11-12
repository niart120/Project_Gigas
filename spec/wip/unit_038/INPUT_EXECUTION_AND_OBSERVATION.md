# U05 M1 入力列と記録付き事後観測

## 1. 目的・状態・起点

状態: **wip**。M0で定めたシリアル入力・有限列・停止・操作記録・事後観測を、GUIやCodexから独立した内部APIとして実装する。

起点は2026-11-12のM1着手依頼、[U05親](../unit_010/INPUT_AND_STOP.md)、[M0設計](../../complete/unit_036/INPUT_AND_STOP_DESIGN.md)。開始時点は `918ba9f`、mainの作業ツリーに変更なし。U04の最新キャッシュ観測・PNG生成・非同期保存を利用する。

利用場面: アプリが保存済み設定を接続へ適用し、通常依頼を記録して操作権を渡す。受け付けた有限列を一つずつ実行し、解除後に取得した画像を返す。停止では残りの列を破棄し、記録完了を待たず解除する。

## 2. 対象範囲

CH552・PokeCon・3DS S2/T3 New Firmwareの設定解決・完全入力状態の変換、専用workerによるシリアル接続、有限列の実行・取消し・解除、runと操作の記録、read開始時計の搬送、記録付き事後観測を扱う。操作権の内部受付・失効も模擬呼出しで検証する。

## 3. 対象外

設定UIと通常ターンへの配線、MCPツールの公開・再読込、実機入力とGUI停止の受入はM2。BT、Goal、手動入力、自動再送・自動復旧、無限マクロは実装しない。

## 4. 関連文書・着手条件

[02 実行管理](../../initial/02-runtime.md)、[03 ゲームI/O](../../initial/03-game-io.md)、[04 記録](../../initial/04-agent-memory.md)、[05 工程](../../initial/05-implementation.md)、[U04 M2](../../complete/unit_035/MCP_OBSERVATION_INTEGRATION.md)に従う。

M0と現在のコード・依存・試験を照合して着手した。入力機器は未接続・実機未検証。serial停止方式はpySerial 3.5のWindows実装を確認してから確定する。MCP公開境界は本子では実装しないため、長い要求の公開設定・実測はM2着手時の条件として引き継ぐ。

## 5. 振る舞い仕様

| 境界 | 契約 |
|---|---|
| 設定 | `serial_protocol`既定ch552、`serial_baudrate`既定None。省略baudだけをprotocol既定へ解決し、候補と検証を一つの定義で共用する。空portは未接続。開く直前に列挙identifierとの完全一致を確認する |
| 入力列 | 正の整数msのpress/waitを持つ有限配列。時間・要素数・合計時間の固定上限は設けない。完全状態を検証し、非対応入力・未知キー・boolや小数の座標/時間を拒否する |
| 所有と停止 | 一つの接続と一つの列を所有する。受付時にGrant・接続・映像・全列を検査し、実行中の別列はBUSY。停止は権限を閉じて操作を取消し、実行部がneutralを送る。各write前の再認可や待ち列は作らない |
| 通信 | 同期open/write/closeを一つのworkerへ直列投入する。完全writeだけを送出済みとし、部分write・例外は結果不明。固定write期限を設けず、収束前に次の接続を開かない。正常停止は接続維持、送信/解除失敗はcloseして明示再適用を待つ |
| 記録 | run開始と操作開始は保存成功を入力の前提にする。操作列・結果・観測IDを記録し、port/baud/protocolを通常イベントへ含めない。結果・画像は共通窓口で非同期保存し、停止を待たせない |
| 事後観測 | 同取得generationで最終解除・末尾wait完了より後にread開始したFrameを5秒以内に選ぶ。停止・取得終了/変更・期限切れを区別する。選んだFrameの符号化まで入力枠を保持し、U04と同じObservationを応答・保存へ渡す |

## 6. TDD Test List

既存の設定・capture・観測・保存試験を先に拡張する。新しい試験ファイルは入力状態・通信・実行サービスの振る舞いに限る。明示Eventで競合位置を制御し、sleepだけで停止完了を推測しない。

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | T01 protocol別の既定・baud検証と設定往復・接続用値の解決 | new | unit/integration | 既存settings試験を拡張。14件の期待失敗→36件成功。09c8c21 |
| refactor-skipped | T02 有限列の全体検査と3形式の完全状態・neutralのbyte列 | new | unit | 未実装importによるred→69件成功。代表byte列・全体拒否・巨大な有限値・不変性。b71a3b1 |
| refactor-skipped | T03 read開始時計が取得・共有領域・readerを通って保持される | new | unit/integration | 既存capture試験の18件期待失敗→37件成功。取得/観測/保存/video結合83件成功。型・lint成功。bf02e63 |
| refactor-skipped | T04 単一serial workerで開閉・送信を扱い、取消し後も進行中writeを回収する | new | unit/integration | 未実装importでred→6件成功。部分write・例外・設定・worker所有・取消し回収。0aced30 |
| green | T05 run/操作と観測を共通窓口で順序保存し、取消し後も回収する | new | integration | 未実装importでred→関連95件成功。記録失敗callback、JSONL不確定追記、Codexと共通保存窓口も確認。構造整理不要 |
| refactor-skipped | T04R 繰り返し取消されても進行中writeを回収してから解除できる | regression | unit | 2回目のcancelで期待失敗1件→7件成功。取消しを記憶してI/O完了回収後に伝える。6c85654 |
| refactor-skipped | T04P pySerialが列挙するOS別native portをそのまま使う | regression | unit | Linux/macOS識別子の模擬列挙で拒否2件→10件成功。OS拒否を削除。4ae5e41。実機未検証 |
| todo | T06 権限・排他・開始記録を満たす列だけを実行し、途中停止で解除する | new | integration | 長い待機、保存待ち、通信失敗、古いGrantを含む |
| todo | T07 操作後のFrameを選び、結果・観測ID・非同期保存を結び付ける | new | integration | 最新cache observe維持、取得変更・期限・符号化競合・停止 |

## 7. 設計メモ

共通実行部は完全入力状態と送出結果を扱い、port・baud・bytesはserial adapterに閉じ込める。未実装BT用の選択肢や追加の状態管理は作らない。受付済み操作はasyncioの取消しで止め、開始済み同期I/Oと保存は所有者が回収する。

シリアル入力部のOS分岐はpySerialへ委ね、各OSのnative portを扱う。M1で確認したpySerial 3.5の`serial/__init__.py`と`serial/tools/list_ports.py`はWindows/POSIXの実装を選択する。Gigas側にWindows専用の拒否条件は置かない。Linux/macOSの列挙・接続・停止・実機は未検証であり、保存・GUIを含むアプリ全体の移植を完了した意味ではない。

### シリアル停止の採用方式

[pySerial 3.5 Windows実装](https://github.com/pyserial/pyserial/blob/v3.5/serial/serialwin32.py)では、writeはOSの完了を待つ。cancel_writeは取消し要求だけで完了待ちを行わず、write開始前の呼出しは後続writeを取り消さない。closeもwriteの完了回収を代行しない。[CancelIoExの契約](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex)も取消しとI/O完了を区別する。

採用する内部実装は、操作取消し後も開始済みwriteの自然完了を回収し、その後neutralを送る方式とする。cancel_writeの競合制御や固定期限を追加しない。write回収前にneutral/close/次接続へ進まず、待機中は停止処理未完了として扱う。ドライバーが戻らない場合の停止時間は保証しない。実際の保持・解除と通信断の挙動はM2の実機受入で確認する。

### MCP公開前に解決する制約

採用Codex 0.167.0の[期限解決](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/codex-mcp/src/connection_manager.rs)は、tool_timeout_sec省略時も300秒の既定期限を使う。0は無効化ではなくゼロ秒であり、[要求実行](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rmcp-client/src/rmcp_client.rs)の経過時間制限はprogress通知では延長されない。これは採用版ソースの確認結果であり、長時間HTTP試験は未実施。

M1の内部APIはこの期限に依存しない。M2では公開前に、時間上限を設けない一要求の契約を成立させるruntime・公開方式を確定する。大きい固定期限への置換、入力の暗黙の分割、非同期受付ツールの追加では解決済みとしない。60秒超の試験だけでも任意の長い有限列の成立証拠にはならない。

## 8. 対象ファイル

`src/gigas/game_io/input/`、`game_io/capture/`、`services/`、`storage/`、対応する既存・追加試験、`pyproject.toml`・`uv.lock`、公開APIのdocs、親・恒常仕様。

## 9. 検証

計画: 各itemのred/greenを確認後にコミットする。最終gateは `uv sync --dev`、`uv lock --check`、`uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`、`uv run pytest tests/unit`、`uv run pytest tests/integration`、`uv build`、`git diff --check`。公開APIの型・docstring・docsと仕様リンクも読む。

実行結果: 開始時の `uv run pytest tests/unit tests/integration -q` は465件成功。T01の `uv run pytest tests/unit/test_settings.py tests/integration/test_settings_io.py -q --basetemp=tmp/pytest-t01` は追加14件の期待失敗を確認後、36件成功。型検査成功。実接続・実機・GUI/MCP結合は本子の対象外であり、模擬境界の成功を実機受入に読み替えない。

## 10. 先送り事項

M2: OK／適用からの接続・切替、停止と質問/承認待ちの配線、MCP公開能力と再読込、60秒超の公開要求の完了・途中停止、対象機器のファーム/baud/reset/解除/事後映像を確認する。BTは[unit_037](../../planned/unit_037/BLUETOOTH_INPUT.md)で扱う。

## 11. チェックリスト

- [x] M0と現在の実装を照合し、対象範囲と対象外を定めた
- [ ] TDD Test Listを検証・コミット付きで完了した
- [ ] 公開API・依存・docsと必要なgateを確認した
- [ ] 未検証事項とM2の着手条件を親へ反映した
