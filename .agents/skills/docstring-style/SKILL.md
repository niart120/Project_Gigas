---
name: docstring-style
description: "公開APIのdocstringやAPIガイドを整える。Google形式の記述、説明の過不足、型注釈との不一致を見直す場面で使う。"
---

# APIの説明

## 説明する範囲

公開APIのdocstringはGoogle形式とし、呼び出し側が判断に使う内容を書きます。形式の設定は `pyproject.toml` のruff設定に従います。

公開範囲は、`__init__.py` のexport、利用ガイドで紹介しているAPI、実際の呼び出し側から確認します。名前が公開形式だからという理由だけで、内部実装の説明を長くする必要はありません。

内部helperは、名前とコードから読み取れない前提がある場合に説明します。例えば時間の単位、呼び出せる状態、資源の所有が引き渡される条件は、短く書いておくと呼び出し側で判断できます。

## Google形式の使い方

冒頭で何を行うAPIかを短く説明し、必要な節を使います。

| 節 | 書く内容 |
|---|---|
| Args | 型注釈だけでは分からない制約、単位、既定値や `None` の意味 |
| Returns | 戻り値の意味。自明な場合は省略 |
| Raises | 呼び出し側が対処すべき例外と、その条件 |
| Examples | 呼び出し方や状態変化を具体例で説明する必要がある場合 |

設定読込APIの説明例です。節は実際の契約に合わせて選びます。

```python
"""Read settings from a TOML file.

Args:
    path: Settings file. Relative paths use the process working directory.

Returns:
    Settings populated with defaults for omitted entries.

Raises:
    OSError: The file cannot be read.
    ValueError: The file contains invalid settings.
"""
```

型注釈の型名をArgsへ書き直すより、相対パスの基準や省略時の扱いを説明します。例外は呼び出し側が対処すべきものを扱い、内部で偶然発生し得る例外を列挙しません。

取消し・close・再接続などのAPIでは、呼び出し後に何が完了しているかも契約です。「処理を停止する」だけで曖昧なら、受付を閉じるのか、実行中の処理や資源解放まで待つのかを説明します。

## 他の文書との分担

APIガイドは利用例や複数APIの使い方、docstringは個々の契約を担当し、同じ説明を二重に維持する量を抑えます。通信形式や保存形式のまとまった説明は `docs/design/interfaces/` や `docs/design/data/` に置けます。

## 確認

```powershell
uv run ruff check .
uv run ruff format --check .
```

説明と実装が食い違う場合は、どちらを直すべきか契約から判断します。形式はruff、意味はコードと呼び出し側を読んで確認します。
