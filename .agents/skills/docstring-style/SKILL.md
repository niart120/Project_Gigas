---
name: docstring-style
description: "この Python repo の public API docstring を Google style、ruff pydocstyle、型注釈、README/docs と整合させる skill。ユーザが docstring、公開 API 説明、Args、Returns、Raises、Examples、README/docs との整合、D lint、pydocstyle の修正を依頼したときに使う。"
---

# Docstring Style

public API の docstring を、利用者が呼び出し方と失敗条件を判断できる契約として書く。

## 対象

必須:

- `src/<package>/__init__.py` から export される関数、class、例外、dataclass。
- CLI entry point の public helper。
- docs / README で紹介している API。
- package が `py.typed` を含む場合、利用者が型を参照する public surface。

内部 helper は、複雑な前提、例外、単位、状態変化がある場合だけ書く。単純な実装説明は書かない。

## 形式

Google style を使う。ruff の `[tool.ruff.lint.pydocstyle] convention = "google"` を前提にする。

```python
def load_config(path: str, *, strict: bool = True) -> Config:
    """Load a configuration file.

    Args:
        path: Configuration file path.
        strict: If true, reject unknown keys.

    Returns:
        Parsed configuration.

    Raises:
        ConfigError: The file is missing, unreadable, or invalid.
    """
```

## 確認規則

- 1 行目は命令文ではなく、何を返す・何を行う API かを短く書く。
- Args には型を書き直さない。型注釈にない制約、単位、default の意味、`None` の意味を書く。
- Returns は戻り値が自明でない場合に書く。`None` を返す command helper では省略してよい。
- Raises は利用者が捕捉すべき例外だけを書く。内部実装の偶発例外を羅列しない。
- Examples は public API、CLI、複雑な状態遷移、エラー処理を説明するときに使う。
- docstring と型注釈が矛盾したら、先に API 契約を決める。必要なら `type-boundary-review` を使う。
- README/docs の説明と docstring が同じ内容を違う言葉で二重管理している場合、README は入口、docstring は API 契約へ分担する。
- 「便利」「簡単」「適宜」ではなく、具体的な入力、戻り値、失敗条件を書く。
- private helper に説明を足して public API の不足を隠さない。

## 手順

1. public surface を確認する: `src/<package>/__init__.py`、`py.typed`、README/docs、対象 spec。
2. `uv run ruff check .` で D 系 rule と ANN 系 rule を確認する。
3. docstring を追加・更新する前に、型注釈と公開契約を確定する。
4. Args / Returns / Raises / Examples を必要な section だけ追加する。
5. `uv run ruff format --check .` で docstring code block の format drift を確認する。

## Commands

```powershell
uv run ruff check .
uv run ruff format --check .
uv run ty check --no-progress
uv run pytest tests/unit
```

対象 API の洗い出し:

```powershell
rg -n "__all__|def |class |Args:|Returns:|Raises:|Examples:" src README.md docs
```

## 報告

```markdown
### Docstring Review

変更:
- API:
- 追加 section:

確認:
- command:
- docs:

残リスク:
-
```
