---
name: pr-merge-cleanup
description: "この Python repo の変更を Git と GitHub CLI（gh）で PR 作成・チェック・マージし、default branch の同期とローカル・リモートのブランチ整理まで行う skill。マージ済み PR の後片付けだけにも使う。"
---

# PR Merge Cleanup

Gitでpush・同期・ブランチ削除を行い、GitHub CLI（`gh`）でPR・CI・レビュー・マージを操作する。GitHubプラグインの有無を実行条件にしない。

## 着手時の確認

- `git branch --show-current`、`git status --short`、`git remote get-url origin`で現在地を確認する。既存変更を破棄しない。
- `gh --version`と`gh auth status`でCLIと既存認証を確認する。トークン値を表示・抽出しない。sandbox内だけで認証が失敗する場合は、許可された通常実行で既存の資格情報を確認し、認証切れと区別する。利用できなければ、その不足を報告し、認証を要しないローカル作業を進める。
- `gh repo view --json nameWithOwner,defaultBranchRef`で対象repoとdefault branchを確定する。以後のgh操作は`--repo <owner/repo>`で対象を明示する。
- PR作成・マージへ進む前に、対象変更をcommitし、worktreeをcleanにする。default branchへの直接commitはユーザーの明示指示がある場合に限る。
- 後片付けだけならdefault branch上から実行してよい。ローカル作業branchが既に削除済みでも、PR情報からリモートの対象を確認できる。

## PR作成・チェック・マージ

1. `agentic-self-review`で実行gateと未検証事項を整理し、`.github/PULL_REQUEST_TEMPLATE.md`に沿うPR本文を一時ファイルへ書く。本文は下の`PR Body`を満たす。
2. `git push -u origin <branch>`で作業branchをpushする。`git log --oneline <default>..HEAD`を本文に含める。
3. `gh pr list --repo <owner/repo> --head <branch> --state open`で既存PRを確認する。なければ`gh pr create --repo <owner/repo> --base <default> --head <branch> --title <title> --body-file <file>`で作成する。既存PRの本文更新も`gh pr edit <number> --repo <owner/repo> --body-file <file>`を使う。
4. `gh pr checks <number> --repo <owner/repo> --watch`で対象HEADのCI完了を待つ。長い待機はバックグラウンド実行して進捗を確認する。required checkに加え、変更範囲に必要なCIも成功していることを確認する。checkがない場合を成功した試験として報告しない。
5. `gh pr view <number> --repo <owner/repo> --json headRefOid,baseRefName,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`でHEAD・base・マージ可否を再確認する。`gh pr view --json reviews`と`gh api graphql`でレビューと未解決reviewThreadsも確認する。reviewThreadsはページを最後まで読み、件数上限による見落としを避ける。
6. 必須checkの未完了/失敗、変更要求、未解決の必須指摘、draft、競合、マージ可否不明があれば解消してから進む。HEADが更新されたらgateとレビューを新しいSHAで確認し直す。
7. `gh pr merge <number> --repo <owner/repo> --merge --match-head-commit <verified-head-sha>`でmerge commitを作る。`--admin`で保護を迂回しない。ブランチ削除は次節で同期・確認後に行う。
8. `gh pr view <number> --repo <owner/repo> --json state,mergedAt,mergeCommit,headRefName,headRefOid,baseRefName,isCrossRepository`で実際のMERGED状態を確認する。merge queueへ入っただけなら完了とせず、状態を追う。

## 同期・ブランチ整理

1. worktreeがcleanであることを確認し、`git switch <default>`、`git pull --ff-only origin <default>`で同期する。失敗したら停止し、force/resetで履歴を合わせない。
2. 対象PRのMERGED状態、base、merge commitが同期先に含まれることを確認する。マージ対象headのSHAとbranch名を保持する。
3. ローカル作業branchが残っていれば、default branchではなく、他のworktreeで使用中でなく、未取り込みの変更がないことを確認して`git branch -d <branch>`で削除する。`-d`が拒否した場合は理由を確認し、`-D`で強制削除しない。
4. `git ls-remote --heads origin <branch>`でリモートを確認する。存在しなければ削除済みとして扱う。存在する場合は、default branchではなく、対象PRのheadと同じrepo・branch・SHAであることを確認する。マージ後の新しいcommitやforkのbranchを誤って削除しない。
5. 確認した作業branchを`git push origin --delete <branch>`で削除する。プラグインの削除APIは不要。もう一度`git ls-remote --heads origin <branch>`を実行し、成功終了かつ該当refがないことを確認する。通信失敗を削除成功と扱わない。
6. `git status --short`とdefault branchのlocal/remote SHAを確認し、PR URL・merge commit・削除branch・gate結果・残った作業を報告する。

## PR Body

- Summary: 変更目的を1〜3行で書く。
- Related: spec、docs、issue、作業指示元。
- Changes: ファイル一覧ではなく論理単位。
- Commit Log: `<default>..HEAD`。
- Testing: commandと結果。未実行gateは理由を記す。
- Agentic SDD Gates: used / not usedと根拠。
- Review Notes: 対象範囲の変更、先送り事項。
