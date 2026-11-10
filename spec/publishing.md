# 配布と公開

現時点ではローカルのwheel・ソース配布物の作成だけを対象とする。PyPIへの公開、名称の確保、Trusted Publishing、公開用workflowは未設定であり、公開手順は未確定。

```powershell
uv build
uv run twine check --strict dist/*
```

配布名は暫定的に `gigas` とする。公開に着手するときは名称の登録可否、公開先、認証方法、隔離環境へのインストール確認を作業仕様に定める。公開にはユーザーの指示を要する。

共通指示リソースの同梱確認は `initial/06-repository.md` と `initial/07-verification.md` に従い、実装時に行う。開発用のAGENTS、Skill、作業仕様をソース配布物へ追加する設定は設けない。
