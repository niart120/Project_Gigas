---
name: pr-merge-cleanup
description: "GitHubのPR作成・更新・CI確認・マージと、マージ後の同期・ブランチ整理を行う。依頼された範囲を扱い、PR作成だけの依頼をマージまで広げない。"
---

# PRとブランチ整理

## 対象の確認

ブランチ、作業ツリー、remote、対象リポジトリのdefault branchを確認します。既存変更を保護し、対象の差分とコミットを把握してから操作します。

Git操作はGit、PR操作は利用可能なGitHub連携または `gh` を使います。以下は `gh` を使う場合の要点です。リポジトリ・PR番号・ブランチ・SHAは確認した値を指定します。

```powershell
git branch --show-current
git status --short
git remote get-url origin
gh repo view --json nameWithOwner,defaultBranchRef
```

以降のコマンド例の `$repo`、`$branch`、`$base`、`$prNumber` は、確認した対象を表します。操作は依頼された範囲まで進めます。

## PR作成・更新

対象の変更をコミットし、作業ブランチをpushします。対象ブランチの既存PRを確認し、なければ作成します。本文はリポジトリのPRテンプレートに合わせ、目的・変更内容・実施した検証をまとめます。関連unitや文書があれば参照できます。

`gh pr create` / `gh pr edit` の本文は一時ファイルに書き、`--body-file` で渡します。作成・更新後はPRの内容と対象ブランチを確認します。

```powershell
git push -u origin $branch
gh pr list --repo $repo --head $branch --state open
```

本文を書いたファイルを `$bodyFile`、タイトルを `$title` とする作成例です。既存PRがある場合は作成の代わりに更新します。

```powershell
# PRがない場合
gh pr create --repo $repo --base $base --head $branch --title $title --body-file $bodyFile
# 既存PRを更新する場合
gh pr edit $prNumber --repo $repo --body-file $bodyFile
```

作業中に範囲が変わった場合、タイトルと本文は最終的な差分に合わせます。

## マージ

マージまで依頼されている場合は、対象HEADのCIとレビュー、未解決の指摘、競合を確認します。レビュー取得にページ分割がある場合は最後まで確認します。修正でHEADが変わったら、新しい差分に対応する確認を行います。

必須checkの失敗・変更要求・競合を解消してから進めます。`gh` では確認済みHEADを `--match-head-commit` に指定し、`--merge` でマージします。`--admin` で保護を迂回しません。

```powershell
gh pr checks $prNumber --repo $repo --watch
gh pr view $prNumber --repo $repo --json headRefOid,baseRefName,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup
```

チェックとレビューを確認したHEADを `$verifiedHead` としてマージします。待機が長い場合は実行中の処理を追跡し、同じ確認を重複起動しません。

```powershell
gh pr merge $prNumber --repo $repo --merge --match-head-commit $verifiedHead
gh pr view $prNumber --repo $repo --json state,mergedAt,mergeCommit,headRefName,headRefOid,baseRefName,isCrossRepository
```

キューへの登録とマージ完了を区別し、実際のMERGED状態とmerge commitを確認します。

## 同期・整理

作業ツリーに未コミットの変更がないことを確認し、default branchへ切り替えて `git pull --ff-only` で同期します。対象PRのmerge commitが取り込まれてから、作業ブランチを整理します。

削除対象はdefault branch以外の作業ブランチです。ローカルは未取り込み変更や他worktreeでの使用がないことを確認し、`git branch -d` を使います。リモートは対象PRと同じリポジトリ・ブランチで、マージ後の新しいコミットがない場合に削除します。force/resetで同期や削除を押し通しません。

確認後の操作例です。一括実行用のスクリプトではなく、各照会結果を見て次の操作へ進みます。ブランチが既に消えていれば、その削除操作は不要です。

```powershell
git switch $base
git pull --ff-only origin $base
git branch -d $branch
git ls-remote --heads origin $branch
git push origin --delete $branch
git ls-remote --heads origin $branch
```

削除前の `ls-remote` で対象SHAを照合します。通信失敗と、成功した照会でrefが存在しない場合を区別します。ローカルの削除が拒否された場合も、未取り込み変更などの理由を確認します。

最後に同期とブランチの状態を確認し、PRへのリンク、マージ・整理の結果、残る作業があれば報告します。
