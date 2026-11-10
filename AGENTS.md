# Project_Gigas Agent Guide

## 対話

- ユーザとの対話は日本語で行う。
- 技術文書と回答は、事実、仮説、提案、未検証事項を分けて書く。
- 未確認の外部仕様、未実行の検証、推測で補った設計判断は確認済みとして扱わない。
- 変更を伴う依頼では、作業前に現在の repo 状態を確認する。

## プロジェクト概要

`gigas` は Python `>=3.12` を前提にしたパッケージである。

| 種別 | 値 |
|---|---|
| 配布名 | `gigas` |
| import ルート | `gigas` |
| 説明 | Windows-native interactive gaming agent system. |
| 起動コマンド | 未実装。U01で定義する |

初期設計の正本は `spec/initial/` に置く。実装、公開 API、CI、リリース、エージェント作業の流れを変更するときは、関連する仕様書と品質ゲートの整合を確認する。

## 作業仕様

このリポジトリは `spec/wip` 型の作業仕様を使う。仕様書は設計メモではなく、対象範囲、対象外、TDD 状態、検証結果、先送り事項を束ねる作業単位である。

| 目的 | パス |
|---|---|
| 初期設計・運用規約 | `spec/initial/` |
| 着手中の作業仕様 | `spec/wip/unit_XXX/FEATURE_NAME.md` |
| 完了した作業仕様 | `spec/complete/unit_XXX/FEATURE_NAME.md` |
| 小さい観測・先送り判断 | `spec/dev-journal.md` |
| 公開手順 | `spec/publishing.md` |

作業仕様には、目的、対象範囲、対象外、関連文書、振る舞い仕様、TDD Test List、対象ファイル、検証、先送り事項、完了チェックリストを含める。

## Agent Skills

リポジトリ内 skill は `.agents/skills` を正本として管理する。`.github/skills` には重複配置しない。利用する手順は以下の一覧と各 `SKILL.md` で確認する。

主な skill:

- `agentic-sdd`: `AGENTS.md` と `spec/initial` から次の作業単位を選び、計画、実装、品質ゲートへ進める。
- `agentic-self-review`: 作業完了前、PR 前、引き継ぎ前に品質ゲート結果と未検証リスクを整理する。
- `diagnosing-bugs`: Matt Pocock 氏の第三者提供 skill。再現困難な不具合と性能退行で、赤にできる再現手順、仮説、計測、回帰テストを順に作る。
- `type-boundary-review`: `ty` の結果、公開 API、標準ライブラリの型構文、`Any` / `Unknown` / `None` / `Protocol` / `TYPE_CHECKING` / `py.typed` の型境界を確認する。
- `docstring-style`: 公開 API の Google 形式 docstring を、型注釈、ruff pydocstyle、README/docs と整合させる。
- `spec-format`: `spec/wip` / `spec/complete` の作業仕様を作成、更新、完了移動する。
- `dev-journal`: 仕様書へ昇格する前の小さい観測や先送り判断を `spec/dev-journal.md` に記録する。
- `tdd-workflow`: TDD Test List から red / green / refactor を進める。
- `tdd-test-list`: 振る舞いベースの TDD Test List を作成、更新する。
- `tdd-one-cycle`: TDD Test List の 1 項目だけを red / green / refactor で進める。
- `refactor-after-green`: green 後に観測可能な振る舞いを変えず構造を整える。
- `tidy-first`: 振る舞い変更と構造変更を分ける。
- `test-desiderata-review`: テスト価値と trade-off を確認する。
- `pr-merge-cleanup`: PR 作成、merge、default branch 同期、branch cleanup を行う。
- `pypi-release`: PyPI / TestPyPI release の preflight、version bump、tag、publish、smoke check を扱う。

## Python

- Python 実行と依存管理は `uv` 経由に統一する。
- Python スクリプトは `python ...` ではなく `uv run python ...` で実行する。
- 依存追加は `uv add <package>`、開発依存は `uv add --dev <package>` を使う。
- パッケージメタデータや依存を変更したら `uv lock` を実行し、`uv.lock` を commit する。
- 型注釈は Python 3.12+ の構文を使う。
- 標準ライブラリで表現できる型を優先し、`3.12` 前提では `typing_extensions` などの互換用パッケージは原則不要。
- 実行時に不要な型だけの import は `if TYPE_CHECKING:` に置く。
- `from __future__ import annotations` は、相互参照や実行時評価の遅延が必要な場合だけ使う。
- public API には Google style の docstring を書く。
- `print` は CLI 境界以外に置かない。ライブラリコードでは戻り値、例外、logging を使う。

## Docs / Public Text

- GUIには文章を原則として記載しない。使い方、UIの意味、機能範囲、操作後の効果を説明する本文・案内文・説明用ツールチップは置かず、利用手順はREADME/docsへ書く。
- GUIのボタン名・項目名・見出し、短い状態名・確認対象・エラー理由、パスや値は必要な識別情報として表示できる。文章を短いラベルへ分割して説明を埋め込む回避はしない。
- Codexが送るテキスト（応答・計画・質問・承認内容等）とユーザーが投入する文章は例外とする。アプリが独自に付け足す説明文は例外に含めない。
- GUIの文言は実画面とコードを読んで確認する。説明文の禁止を、自然言語の固定文字列や句読点だけを検査するテストへ置き換えない。

- README は利用開始の入口として短く保ち、詳細な API 契約、実機ログ、agent 運用、作業履歴は `docs/`、`spec/`、`AGENTS.md` に分ける。
- 公開 docs には、利用者が確認できる現在の仕様、手順、制約を書く。開発者向けメタ表現や対話内だけの文言を残さない。
- public API、package metadata、release の挙動を変更したら、docstring と利用者向け docs の更新を完了条件に含める。
- docs test が落ちた場合は、文書の誤りとテスト設計の誤りを同列に調査する。自然言語の固定文字列、見出し、説明順、禁止語、checkbox、状態語だけを検査する assertion は原則追加しない。
- 自動検査へ落とすのは、Markdown link、参照先、frontmatter、YAML/TOML 構文、schema、生成物、実行可能な製品契約など、機械的に観測できる契約に限る。説明の正確さ、読みやすさ、根拠、未検証表示は対象文書を読んで確認する。
- 文書の変更と固定期待文字列を同じ変更で追加しても、その一致だけを意味上の整合確認の根拠にしない。変更対象を読まない test の成功も、当該文書の検証結果に含めない。
- docs site や Pages 公開を対象範囲に含めた場合、local build だけで完了にしない。remote workflow、deployment、公開 URL の確認を根拠に含める。
- 公開 API の型注釈、`py.typed`、`# type: ignore`、`cast()` を変更した場合は `$type-boundary-review` を使う。
- 公開 API の docstring、README/docs の API 説明、D 系 lint を変更した場合は `$docstring-style` を使う。

## テストと検証

製品コード、package metadata、依存、テスト基盤を変更した場合は、変更範囲に対応する次の gate を選ぶ。すべての command を機械的に実行するのではなく、実行した command が何を検査するかを検証記録に書く。

```powershell
uv sync --dev
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check --no-progress
uv run pytest tests/unit
uv build
git diff --check
```

対象となる integration test tree がある変更では、`uv run pytest tests/integration` も実行する。対象 tree がまだない場合、該当 gate は未実行理由を報告する。

docs / spec / skill だけの変更では、対象文書の事実整合、根拠、未検証表示、リンク・参照先、仮テキスト残りを確認する。リンク、frontmatter、schema などの機械的契約に対応する command がある場合だけ実行する。変更対象を検査しない unit test、type check、package build はローカル必須 gate にせず、その成功を文書品質の根拠にしない。

repo-local skill を変更した場合は、実行環境の skill-creator に付属する scripts/quick_validate.py の所在を確認し、その絶対パスを環境変数 SKILL_VALIDATOR に設定する。

```powershell
uv run --with pyyaml python -X utf8 $env:SKILL_VALIDATOR .agents\skills\<skill-name>
```

## Git / PR

- 変更を伴う作業では開始時に branch と `git status --short` を確認する。
- default branch への直接 commit は、ユーザの明示指示がある場合を除き避ける。
- dirty worktree では既存変更を読んで、ユーザ変更を破棄しない。
- commit は 1 つの論理変更に絞る。
- PR 本文には `.github/PULL_REQUEST_TEMPLATE.md` を使い、実行 command と結果を具体的に書く。
- `not run` と `not applicable` を混同しない。
- PR merge 後は default branch を同期し、安全な場合だけ作業 branch を削除する。

Conventional Commits に準拠する。

```text
<type>(<scope>): <subject>
```

type は `feat` / `fix` / `docs` / `style` / `refactor` / `perf` / `test` / `build` / `ci` / `chore` / `revert` を使う。subject は日本語で記述し、末尾句点は付けない。
