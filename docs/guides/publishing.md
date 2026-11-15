# 配布と公開

現在はローカルのwheel・ソース配布物を作成できます。配布名は`gigas`です。PyPI向けの公開workflowはリポジトリにありません。

```powershell
uv sync --locked --dev
uv build
uv run twine check --strict dist/*
uv run python scripts/check_package.py
```

`uv build`は`dist/`へ配布物を作成します。`twine check`は配布メタデータとREADMEの形式を検査します。`check_package.py`は別の一時領域にビルドし、同梱資材と隔離環境へのインストールを確認します。検査対象の詳細は[スクリプト](../../scripts/check_package.py)を参照してください。

wheelからの起動は[README](../../README.md#wheelから起動)を参照してください。

公開する際は、公開先での名称の利用可否、認証方法、配布バージョンを確認します。PyPI・TestPyPIへのアップロードは公開の依頼を受けたときに行います。公開後は対象バージョンのインストールと起動を確認します。
