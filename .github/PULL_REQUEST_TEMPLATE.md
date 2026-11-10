## Summary

<!-- 変更内容を1-3行で要約する。背景や動機は Related に寄せる。 -->

## Related

<!-- Issue、spec、プロンプト、参照 docs。agent 作業では指示元を記載する。 -->

- closes #
- spec:

## Changes

<!-- ファイル名の羅列ではなく、論理的な変更単位を書く。 -->

-

## Commit Log

<!-- `git log --oneline main..HEAD` または default branch に合わせた範囲を貼る。 -->

```text
<git log --oneline main..HEAD の出力>
```

## Testing

<!-- 実行した command と結果を書く。未実行の場合は理由を書く。 -->

```text
uv sync --dev
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check --no-progress
uv run pytest tests/unit
uv build
git diff --check
```

## Agentic SDD Gates

<!-- Agentic SDD を使っていない場合は not used と書く。 -->

- Work Unit:
- Intent Delta:
- Non-goals:

| gate | result | evidence |
|---|---|---|
| Requirements |  |  |
| Plan / Tasks |  |  |
| Tests |  |  |
| Static |  |  |
| Package |  |  |
| Integration Review |  |  |

## Checklist

- [ ] lint / format チェック通過
- [ ] 型チェック通過
- [ ] unit test 通過
- [ ] package build 通過
- [ ] commit prefix が変更の動機と一致している
- [ ] 新規・変更コードに対するテストを追加した、または不要理由を書いた

## Notes

<!-- 既知のリスク、未検証事項、代替案。なければ none。 -->

- none
