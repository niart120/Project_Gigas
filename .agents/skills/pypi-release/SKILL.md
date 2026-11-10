---
name: pypi-release
description: "この Python repo の PyPI release を計画・実行する workflow skill。ユーザが PyPI / TestPyPI 公開、バージョン更新、release PR、v* tag、GitHub Actions publish、公開後 smoke check、release 手順確認を依頼したときに使う。"
---

# PyPI Release

`spec/publishing.md` を release runbook の正本として使う。手順詳細を skill に重複させない。

## 手順

1. release 計画または実行前に `spec/publishing.md` を読む。
2. `.github/workflows/publish.yml`、`pyproject.toml`、`uv.lock`、git 状態を確認する。
3. release PR 作成、merge、default branch 同期、branch cleanup は `pr-merge-cleanup` に委譲する。
4. local gate、TestPyPI smoke、production publish、post-publish smoke を分けて記録する。

## 停止条件

- local `twine upload` は使わない。
- current turn の明示確認なしに production tag push や `target=pypi` workflow を実行しない。
- `spec/publishing.md` または `.github/workflows/publish.yml` がなければ停止する。
- candidate version、tag、Trusted Publisher 設定、local gate、CI、publish workflow が runbook と矛盾する場合は停止する。
- PyPI に同一 version が存在する場合は停止する。

## 報告

version、release branch / PR、tag、workflow run、PyPI / TestPyPI URL、gate、smoke、停止条件を報告する。
