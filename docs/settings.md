# グローバル設定

設定は Windows ユーザーごとに `%LOCALAPPDATA%\GIGAS\settings.toml` に保存します。文字コードは UTF-8 です。手で編集するときは、GIGAS を終了してからファイルを開いてください。起動中に編集した値は、アプリからの保存で上書きされる場合があります。

```toml
[capture]
device = ""

[controller.serial]
port = ""

[agent]
model = ""
effort = ""
auth_method = "chatgpt"
```

| 項目 | 意味 |
|---|---|
| `capture.device` | 映像取得元の選択値 |
| `controller.serial.port` | シリアルポート名。例は `COM3` |
| `agent.model` | 使用したいモデル |
| `agent.effort` | 使用したい推論エフォート |
| `agent.auth_method` | 認証方式。指定できる値は `chatgpt` と `api_key` |

項目を省略すると例の値を使います。空文字は未指定を表します。`auth_method` は方式だけを記録します。API キーやトークンはこのファイルへ書かないでください。

初期開発中は項目名・型・既定値が変わることがあります。更新後にファイルを読めなくなったら、エラーに示された箇所を修正してください。作り直す場合は、既存ファイルを退避すると次回の読込で既定値を使えます。

## Python から読み書きする

```python
from dataclasses import replace

from gigas.storage.settings import default_settings_path, load_settings, save_settings

path = default_settings_path()
settings = load_settings(path)
save_settings(path, replace(settings, serial_port="COM3"))
```

`default_settings_path()` は保存先だけを返します。テストでは一時ディレクトリ内の `Path` を `load_settings()` と `save_settings()` に直接渡せます。

`load_settings()` はファイルがなければ既定値を返します。ファイルは明示的な保存時に作られます。`save_settings()` は既存の TOML を読み、既知の項目を更新します。変更しない項目とコメントを保ち、変更する文字列を空にする場合も同じ行のコメントを残します。

パスの決定、読込、検証、書込に失敗した場合は `SettingsError` を送出します。既存ファイルの構文や現在の項目が不正な場合、保存を中止してファイルを保持します。一時ファイルの後始末に失敗したときは、例外にその情報も付記します。
