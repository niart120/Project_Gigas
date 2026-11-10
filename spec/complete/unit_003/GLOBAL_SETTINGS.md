# U01 M1 グローバル設定とTOML永続化

## 1. 概要

### 1.1 目的と状態

[親仕様](../../wip/unit_001/APP_STARTUP.md)のグローバル設定を、GUI・機器・Codexから独立して保存・再読込できる部品として実装した。2026-11-10完了。[M0](../unit_002/ARCHITECTURE_AND_DEPENDENCIES.md)で決めたTOML KitとUI非依存の保存境界を使う。

### 1.2 起点と要求差分

[04の共通設定](../../initial/04-agent-memory.md)とユーザーのTOML指定を起点とする。今回の指示に従い、グローバル設定は破壊的変更を許容し、旧形式との互換性を担保しない。schema_version、版ごとの分岐、旧キーの別名対応、自動移行を作らない。パッケージ自体のバージョンやU02の記録形式は、この判断の対象外。

利用者はアプリ終了中に設定を手編集する。アプリが対応する現在の項目を読み、GUIで編集・保存できればよい。項目が変わった場合は、利用手順に変更点を記し、必要なら利用者が設定を編集・退避して作り直す。旧設定の自動変換は行わない。

## 2. 対象範囲

現在必要な設定項目、その型と既定値、保存先、TOML読込・更新・保存失敗の通知を対象とする。直接依存はM0で選定したtomlkit==0.15.2。実装時は公開型にtype-boundary-review、docstringにdocstring-styleを適用する。

## 3. 対象外

設定画面・Qtワーカー、機器列挙・接続、認証情報保管、過去版との互換性、汎用設定スキーマ基盤、実行中のファイル監視・外部編集検出・競合解決、複数プロセスからの同時編集を含めない。将来候補の項目を先に全実装することもしない。

## 4. 関連文書・NyXの調査

責務は[06](../../initial/06-repository.md)、将来の映像・入力条件は[03](../../initial/03-game-io.md)に従う。storageが読み書きを所有し、appは保存先と編集値を渡す。uiへTOMLDocumentや汎用の文字列キー操作APIを公開しない。

### 4.1 調査対象と分かったこと

指定されたNyXのmasterを確認し、参照版を`648f860e5afd582f80aa58645570a16bfc68a393`（2026-11-09、0.3.1準備）へ固定した。以下はソース読取による確認で、NyXの実行・実機検証はしていない。

- [global_settings.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/settings/global_settings.py)：映像、シリアル、実行、ログ、GUIの項目を定義。schema_versionはないが、旧Bluetooth設定を移行する処理はある。
- [schema.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/settings/schema.py)：型・既定値・選択肢・秘密値を扱う共通基盤。検証時に通常の辞書へ変換するため、TOML Kitを使っていることだけではコメント保持の根拠にならない。
- [workspace.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/settings/workspace.py)と[secrets_settings.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/settings/secrets_settings.py)：workspaceの.nyxpy配下と別の秘密設定を使う。GIGASのグローバル設定・プロジェクト分離とは異なるため配置を流用しない。
- [device_tab.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/gui/dialogs/settings/device_tab.py)：映像は列挙名、シリアルは選択した識別子を保存。取得FPSとプレビューFPSは別。プロトコル選択に応じた通信速度の既定値変更がある。

### 4.2 入ってきそうな設定と追加時期

NyXの既定値は参考値であり、GIGASでの適切さを確認した値ではない。

| 分類 | NyXで確認した項目・既定値 | GIGASでの扱い |
|---|---|---|
| 映像取得元 | capture_device（空文字） | M1にcapture.deviceとして選択値を保存。実際の識別方法はU03で変更可能 |
| 取得と表示 | capture_fps（未指定）、preview_fps（60） | U03の候補。取得速度と表示速度を分け、機器で確認して追加 |
| 新フレーム待機 | runtime.frame_ready_timeout_sec（3.0） | U03/U05の候補。03の新フレーム待機契約に対応。NyXの3秒をそのまま保証にしない |
| シリアル | controller.serial.device（空）、protocol（CH552）、baudrate（9600） | M1はcontroller.serial.portのみ。U04で実デバイスに合わせprotocol・baudrate・必要なtimeoutを追加 |
| ログ・GUI | logging.file_level/gui_level、ローテーション・保持期間、gui.window_size_preset | 診断ログは必要となった工程、画面状態はM2以降で判断。U02のevents.jsonlを自動削除する設定へ転用しない |
| GIGAS固有 | NyXにはCodexモデル・エフォート・認証方式の定義なし | M1はagentの希望値を保存し、U06で有効値を確認 |

初期GIGASへ持ち込まない項目は、window/captureの別取得方式、ponkan・N3DS用の設定、controller.swbt、preview_touch、通知サービスの秘密値、runtime.allow_dummy。03・05のカメラとシリアルを使う範囲を守り、製品の実行経路でダミー成功を許可する設定は作らない。

呼出し先の[SerialComm](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/hardware/serial_comm.py)でも通信速度の受渡しを確認した。ただしread側のtimeout指定を、GIGASが必要とするwrite timeoutの確認と混同しない。[camera_capture.py](https://github.com/niart120/Project_NyX/blob/648f860e5afd582f80aa58645570a16bfc68a393/src/nyxpy/framework/core/hardware/camera_capture.py)はdevice_index・api_pref・fpsを受け取る。保存する選択値と実際の取得APIの引数が同じとは限らず、変換はU03の機器境界で扱う。

03から追加候補になるのは、観測画像の解像度・切出し、新フレーム待機期限、入力列の要素数・合計時間上限、シリアルwrite timeout、MCPの接続・ツール期限。これらはNyX設定で確認済みの項目と区別し、U03〜U06で必要なものを設定または内部定数として決める。選択肢が増え得るという理由だけでbackend登録機構を作らない。

## 5. 振る舞い仕様

### 5.1 変更方針と現在の項目

保存先は`%LOCALAPPDATA%\GIGAS\settings.toml`、UTF-8。設定の名前・型・既定値は必要に応じて変更してよく、旧形式の読込や同じ挙動を保証しない。今ある項目にだけ型と既定値を定める。

```toml
[capture]
device = "" # 接続時に選択・確認する取得元

[controller.serial]
port = "" # 例: COM3。保存だけでは接続しない

[agent]
model = "" # 空欄は未指定
effort = "" # 有効な値は接続時に確認
auth_method = "chatgpt"
```

M1では上記5項目を文字列として扱い、省略時は例の値を使う。auth_methodはchatgpt/api_key、他の4項目は空文字を許す。未設定へ戻す操作は空文字への更新とし、同行コメントを保持する。表であるべき箇所の文字列など、現在の既知項目の型が不正なら読込失敗とする。機器名等へ一律の文字列正規化は行わず、接続時に必要な検証は各接続部へ置く。

capture.deviceはU01で保存する選択値であり、永続的な機器IDの保証ではない。U03で列挙・同名機器・再接続を扱う時に構造を変更してよい。controller.serialは通信方式を示す配置で、複数backendの実装を意味しない。プロトコルと通信速度は実デバイス未選定のためM1に既定値を置かない。

未知・廃止キーは使用せず、特別な移行や警告一覧を返さない。同じ形式の文書更新では既存の未知項目を触らず残すが、これは旧キーの意味や将来の保存形式を保証する契約ではない。必要な構造変更時には削除してよく、旧設定で期待どおり動くことを約束しない。不足項目が既定値になった場合も、旧版の意味を復元する処理はしない。

構文や現在の既知項目が不正なら自動初期化せず、パスと理由を返す。利用者は終了後にファイルを修正するか、退避・削除して既定値から作り直せる。破壊的変更の許容と、読込失敗時の無断上書きは別に扱う。認証キーやトークンは通常設定へ保存せず、エラーやログにファイル本文を出さない。

### 5.2 読込と編集

| 状態・操作 | 期待結果 |
|---|---|
| ファイルなし | 既定値を返し、ディレクトリやファイルを作らない |
| 有効なTOML | 現在の既知項目を読み、欠落を既定値で補う。文書の解析と更新はstorage内部で行う |
| 構文・文字コード・既知項目の型不正 | 理由付き失敗を返し、原本を変更しない |
| GUIから保存 | 現在の既知値を元文書へ反映し、未編集部分とコメントを保持 |
| 保存失敗 | 保存成功を返さず、呼出し元が編集値を残して再試行できる |

呼出し側には単純な設定値と読込・保存操作を提供する。TOML Kitの文書はstorage内の処理に使い、uiには設定値を渡す。

手編集はアプリ終了中に行い、起動中の外部変更は検出しない。起動中に手編集した内容はGUI保存で上書きされ得る。この制約を利用手順へ記載し、mtime/digest比較や再読込必須の競合状態を設けない。アプリ内のI/OはM0の1件ずつの実行を使う。

### 5.3 保存先と失敗

保存先を注入可能にし、試験では一時ディレクトリを使う。LOCALAPPDATAが空・未定義・相対パスなら理由付き失敗とし、cwdへ代替保存しない。保存先ディレクトリは明示保存時に作成する。

同じディレクトリの一時ファイルへ書き、閉じてから置換する。原本を先に削除しない。置換成功後だけ保存成功とし、失敗時は原本を保持する。残った自分の一時ファイルは後始末し、後始末の失敗も診断可能にするが、専用の状態機械や例外階層は作らない。電源断やプロセス強制終了時の永続化までは保証しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | ファイルなしで既定値を返し、ディレクトリを作らない | new | unit | 最初の項目。redは設定モジュール未実装、greenは対象1件 |
| refactor-skipped | 現在の値を保存して別の読込で再現する | new | integration | 日本語・空白を含む一時パス。redは保存API未実装、greenは対象1件。lock確認とbuildも成功 |
| refactor-skipped | 欠落は既定値、未知キーは未使用、既知値の不正は失敗となる | edge | unit | 旧形式変換を期待しない。redは検証エラーAPI未実装で5件失敗、greenは対象7件 |
| refactor-skipped | 構文・文字コード不正を返し原本を変えない | edge | integration | 保存へ進めない。redは未変換の構文・文字コード・I/O例外で3件失敗、greenは対象4件 |
| refactor-skipped | 値の更新・クリアでコメントと未編集部分を保持する | new | integration | M0の観測を本保存処理で確認。redはコメント消失と不正文書の上書きで3件失敗、greenは結合7件と設定単体7件 |
| refactor-skipped | 書込・置換失敗で原本を保持し、成功を返さない | edge | integration | 失敗点を注入し一時ファイルも確認。redは一時ファイル作成・置換失敗を扱わず2件失敗、greenは対象9件 |
| refactor-skipped | 保存先が決まらなければ別の場所へ書かない | edge | unit | 実ユーザー設定を使用しない。redは保存先API未実装で4件失敗、greenは対象11件 |

## 7. レビュー結果と設計判断

| 優先度 | 指摘 | 根拠・対応 |
|---|---|---|
| P1 | 初稿5.1のschema_versionと未対応版拒否は、変更の多い初期設定へ互換性管理を先行導入していた | ユーザー方針に従い削除。版分岐・移行・旧名対応を対象外に明記 |
| P2 | capture.preference/input.portだけでは後続設定の見通しが不足していた | NyXの項目と呼出し側を確認し、分類・追加工程を第4節へ記録。deviceとcontroller.serialへ整理 |
| P2 | バイト比較・不透明基準・競合状態は、終了中の手編集には不要 | M1・親・M2から外部変更検出を外す。保存中の二重I/O抑止は維持 |
| P2 | 未知キーの警告一覧と汎用スキーマを想定した説明が設定基盤を重くしていた | 現在の5項目の型と既定値のみ定義。未知キーは使わず触らない |
| 維持 | TOMLコメント保持・現在の型検証・一時ファイル置換は互換性維持とは別の責務 | M0と04の手編集方針、U01の保存成功・失敗の契約に必要なため残す |

NyXから参考にするのは項目の分類と実際の利用箇所であり、汎用SettingsSchema、SecretsSnapshot、RLock、初回読込での自動保存、非厳格読込で既定値へ戻す処理、旧swbt移行はコピーしない。TOML Kitを使う点は共通でも、GIGASでは元文書を保持して編集するM0の判断を維持する。

### 7.1 実装時の判断

- 保存先は `default_settings_path()` で決定する。`load_settings(path)` と `save_settings(path, settings)` は呼出し側から保存先を受け取る。設定値は変更不可の `Settings` とし、M2では編集結果を別の値として渡せる。
- 保存時に既存ファイルを読み直し、現在の形式として検証してから、値が異なる項目と省略されていた項目を書き込む。TOML文書を操作の間に保持する必要がなく、直接保存を呼んだ場合も不正な原本を上書きしない。保存前の再読込は形式確認とコメント保持のためで、競合検出は契約に含めない。
- CRLF入りのファイルをテキストモードで書き戻すと、Windowsの改行変換で空行が増えた。読込・保存をUTF-8のバイト列で行い、コメント保持の結合試験で確認した。
- パス決定・読込・保存の失敗は `SettingsError` に統一した。構文エラーには行・列、文字コードエラーにはバイト位置を含める。一時ファイルの後始末に失敗した場合は同じ例外へ付記する。

## 8. 対象ファイル

`src/gigas/storage/settings.py` とパッケージ入口、`tests/unit/test_settings.py`、`tests/integration/test_settings_io.py` を追加した。`pyproject.toml` と `uv.lock` に `tomlkit==0.15.2` を追加した。Windows CIの試験対象に `tests/integration` を加えた。READMEを入口とし、設定例とPythonからの利用手順は[グローバル設定](../../../docs/settings.md)に記載した。

## 9. 検証

実装前の仕様は `c41c82c` に保存した。各TDD項目は `uv run --locked pytest tests/unit/test_settings.py -q` または `uv run --locked pytest tests/integration/test_settings_io.py -q` でredとgreenを確認し、試験・実装・第6節の記録を同じコミットへ入れた。

| コマンド | 結果と確認内容 |
|---|---|
| `uv add 'tomlkit==0.15.2'`、`uv lock`、`uv sync --locked --dev`、`uv lock --check` | 成功。採用版の追加、依存定義とlockの一致、開発環境の同期 |
| `uv run --locked ruff format --check .`、`uv run --locked ruff check .` | 成功。フォーマット、lint、公開APIのGoogle形式docstring |
| `uv run --locked ty check --no-progress` | 成功。外部から受け取るTOML値を検証して文字列へ絞り、公開APIにTOML Kit型を露出していない。型検査の抑制やcastの追加なし |
| `uv run --locked pytest tests/unit`、`uv run --locked pytest tests/integration` | Windows x64・Python 3.12.10で単体13件、結合9件成功 |
| `uv sync --locked --dev --python 3.13`、`uv run --locked --python 3.13 pytest tests/unit tests/integration` | 作業ツリー内の別環境を使い、Windows x64・Python 3.13.5で22件成功 |
| `uv build` | wheel・sdist生成成功 |
| `uv run --locked pytest tests/unit tests/integration` | CIと同じ試験対象をPython 3.12.10で実行し22件成功 |
| 文書・YAML・wheelの検査 | 仕様のローカル参照62件と1 unit 1仕様書を確認。利用手順のリンクとTOML・Python例、CIのYAML、wheel内の設定モジュールと依存メタデータを確認 |
| `git diff --cached --check` | 空白エラーなし |

結合試験では一時ファイル作成と置換の失敗を注入した。電源断、実ファイルのアクセス権構成、一時ファイル削除失敗の実機再現とGitHub Actions上での実行は未実施。Qt・機器・Codex・配布物からの起動は担当工程で確認する。skill validatorはskill変更なしのためnot applicable。

## 10. 先送り事項

映像の識別方法・FPS・待機期限はU03、シリアルのプロトコル・通信速度・書込期限はU04、MCPと入力列の上限はU04/U05、モデル有効値と認証はU06で決める。必要になった項目だけを追加し、既存設定の互換対応を完了条件にしない。設定画面と配布物はunit_004・005が担当する。

## 11. 完了チェックリスト

- [x] NyXの設定項目とGIGASでの採否・追加工程を整理した
- [x] 互換性を担保しない方針と、残す読込・保存契約を分けた
- [x] 現在の設定の読込・更新・失敗を実装・検証した
- [x] 公開型・docstring・利用手順を揃えた
- [x] 依存lock・buildを含むゲートと未検証事項を記録した
- [x] 本unitを完了へ移した
