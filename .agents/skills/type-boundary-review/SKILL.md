---
name: type-boundary-review
description: "この Python リポジトリの `ty` 型検査、公開 API の型境界、標準ライブラリの型構文、`Any` / `Unknown` / `None` / `Protocol` / `TYPE_CHECKING` / `py.typed` の扱いをレビューする skill。ユーザが ty、型検査、型注釈、型エラー、公開 API の型、type ignore、typing、Protocol、None 境界の見直しを依頼したときに使う。"
---

# Type Boundary Review

型注釈を増やす作業ではなく、利用者に見える境界と保守上の危険箇所を `ty` 前提で確認する。互換用パッケージや `__future__` import は、想定する `Python 3.12/3.13+` 前提では原則不要で、下位互換を追加する場合のみ使う。

## 対象

優先順:

1. public API: `__init__.py` から export される関数、class、dataclass、Protocol、例外。
2. CLI 境界: `main(argv: Sequence[str] | None = None) -> int` のように入力と終了値を明示する。
3. I/O 境界: filesystem、network、subprocess、環境変数、JSON、設定ファイル。
4. callback / plugin / adapter 境界: 呼び出し側と実装側の責務を `Protocol` で表す。
5. test helper 境界: fixture が返す型、fake / stub の public surface。

## 確認規則

- `ty` の結果を起点にする。推測で型を広げない。
- public API は、引数、戻り値、例外、状態変化が docstring と一致しているか確認する。
- `Any` は意図的な escape hatch としてだけ使う。型が分からないだけなら `object`、`Protocol`、`TypedDict`、具体的な union を検討する。
- `None` を返す可能性がある値は `T | None` にし、呼び出し側で分岐する。`cast()` で握りつぶさない。
- `dict[str, object]` のまま渡し回すより、公開 payload には `TypedDict`、dataclass、pydantic model などの境界型を使う。
- 標準ライブラリの型機能を優先し、`typing_extensions` などの互換用パッケージは `requires-python` で必要な場合だけ使う。
- 実行時に不要な型だけの import は `if TYPE_CHECKING:` に置く。
- `# type: ignore` や `# ty: ignore[...]` は最後の手段。使う場合は rule-specific にし、なぜ直せないかを近くに書く。
- `from __future__ import annotations` は、相互参照や実行時評価の遅延が必要な場合だけ使う。循環 import 回避だけなら `TYPE_CHECKING` と文字列注釈で足りることが多い。
- `py.typed` がある package では、利用者に見える型を「内部都合の型」ではなく公開契約として扱う。

## 手順

1. `pyproject.toml` の `[tool.ty]` と package metadata を確認する。
2. `git diff --name-only` で変更された Python ファイル、`py.typed`、`__init__.py`、docs を確認する。
3. `uv run ty check --no-progress` を実行し、出力を rule / file / boundary に分類する。
4. 型エラーを、public API の契約不足、内部実装の推論不足、外部依存の型不足、test helper の曖昧さに分ける。
5. public API に触れた場合は `docstring-style` で docstring および公開 docs と一致させる。
6. 修正後に `uv run ty check --no-progress` と関係する test を再実行する。

## Commands

```powershell
uv run ty check --no-progress
uv run ruff check .
uv run pytest tests/unit
```

狭い確認:

```powershell
uv run ty check --no-progress src tests
rg -n "Any|cast\\(|type: ignore|ty: ignore|TYPE_CHECKING|Protocol|TypedDict" src tests
```

## よくある修正

- callback 引数が広すぎる: `Callable[..., Any]` ではなく、必要な引数と戻り値を持つ `Protocol` を定義する。
- JSON payload が曖昧: `dict[str, object]` から `TypedDict` または dataclass へ寄せる。
- optional 値を未確認で使う: `if value is None:` で戻るか例外に変換する。
- import cycle: 実行時 import を増やさず、`if TYPE_CHECKING:` と必要に応じた文字列注釈を使う。互換用の future import を習慣で追加しない。
- test fake が実装詳細を持ちすぎる: production が見る interface だけを fake に実装する。

## 報告

```markdown
### Type Boundary Review

指摘:
- [severity] file:line: 境界 / 問題 / 修正方針

確認:
- command:
- public API:
- ignore / cast:

残リスク:
-
```
