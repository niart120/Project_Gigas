# 開発環境と検査

Windows、Python 3.12以上3.16未満、uvを使用します。現在のCIはPython 3.12・3.13を対象としています。

```powershell
uv sync --locked --dev
uv run gigas
```

## 検査コマンド

| コマンド | 確認する内容 |
|---|---|
| `uv lock --check` | メタデータと依存ロックの整合 |
| `uv run ruff format --check .` | Pythonコードの整形 |
| `uv run ruff check .` | 静的検査・docstringの形式 |
| `uv run ty check --no-progress` | 型の整合 |
| `uv run pytest tests/unit tests/integration` | 単体試験とアプリ・通信・機器境界の結合試験 |
| `uv run python scripts/check_package.py` | wheel・ソース配布物の構成とインストール後の利用 |
| `git diff --check` | 差分の空白エラー |

変更に関係する検査を選びます。文書だけの変更では内容と参照先を確認し、製品テストの成功を説明の正確さの根拠にしません。

GUIを表示せず試験する場合は、PowerShellで`$env:QT_QPA_PLATFORM = "offscreen"`を設定します。実画面や実機の挙動は別に確認します。Codex・ゲーム機器を使う確認は[実接続の確認手順](verification.md)を参照してください。

依存・Python対応範囲は[pyproject.toml](../../pyproject.toml)、CIの実行条件は[ci.yml](../../.github/workflows/ci.yml)、配布物検査の範囲は[check_package.py](../../scripts/check_package.py)に定義されています。
