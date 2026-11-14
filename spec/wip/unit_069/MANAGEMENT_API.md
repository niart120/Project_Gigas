# U08 M1 専用home・設定・管理API

## 1. 目的・状態

状態: **wip**。[U08親仕様](../unit_013/RESUME.md)のM1を担当する。M0の[確定設計](../../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)を製品の起動・設定・管理APIへ反映し、既存の新規実行・停止を維持する。

利用者はGIGAS専用のCodexへ接続・ログインし、Project・会話・履歴・Goalの保存情報を公開APIから利用する。履歴を読むだけでは会話を実行しない。

## 2. 対象範囲・対象外

専用homeによる起動、GIGAS設定の保存、Projectのcreate/list/read、所属・未所属thread一覧とread、turn/itemページ、Goalのget/clearを扱う。認証・設定のコピーや同期、位置台帳、独自履歴保存は設けない。

記録部品と新規開始の置換はM2、設定の実行時適用と標準指示はM3、選択と実行対象の分離・再接続はM4、会話管理UIはM5。後続の未着手子仕様は作らない。M1で表示や記録の独自実装を先行追加しない。

## 3. 前提・関連文書

M0はcomplete。採用版は0.167.0。契約は[02 実行](../../initial/02-runtime.md)、[04 保存・設定](../../initial/04-agent-memory.md)、[08 公開経路](../../initial/08-sdk-compatibility.md)、受入は[07](../../initial/07-verification.md)に従う。APIの入出力は同梱runtimeから生成したschemaと採用版ソースに照合する。

## 4. 振る舞い仕様

| 境界 | 入力・状態 | 結果 |
|---|---|---|
| 起動 | GIGASの設定ファイル配置 | 同じアプリ領域のcodex_homeを接続生成に明示指定。親プロセスのCODEX_HOMEを継承せず、認証等をコピーしない。接続前は起動・保存しない |
| 設定 | Web検索・標準記憶・承認方法 | settings.tomlに保存。Web/記憶は既定ON、承認者はauto_review既定/user選択。記憶OFFでも既存ファイルを削除しない。実行への適用はM3 |
| Project | 1件の絶対rootをcreate、list/read | サーバー応答を返し、別の位置正本を作らない。実ファイルの作成や選択は呼出し側の責務 |
| 会話 | 所属IDまたは未所属を指定、保存済みthread ID | 公開一覧・metadata・履歴ページを取得。resume・MCP・writer・機器操作を起動しない |
| Goal | 保存済みthreadのget/clear | 現在Goalを取得・削除。実行準備を要求せず、削除は停止ではない |

## 5. 設計・責務

接続と認証・単一受信はCodexConnection、プロセス構築はlaunch、設定永続化はstorage/settingsが所有する。管理要求の形式・応答変換は会話実行とは独立したruntime/codexの部品へ置く。管理APIのためにConversationExecutionやCodexServiceThreadへProject/履歴の状態・変換を集中させない。

各TDD itemのgreen後に依存方向・重複・クラスの責務を確認し、必要な整理と同じ試験の再実行を記録する。ファイル分割だけの空基盤や、未実装を成功にする仮経路は追加しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | I1: 既定サービス接続はGIGAS専用homeを使い、接続前は作成せず、開発用homeを変更しない | new | integration | 起動・終了と既存接続の回帰を確認、I1 commit対象 |
| todo | I2: Web・記憶・承認者の既定値、保存往復、不正値拒否、他設定の保持 | new | unit/integration | UIと実行への適用はM3/M5 |
| todo | I3: Project create/list/readは唯一のrootと公開応答を扱い、RPC失敗を伝える | new | unit/integration | schema照合、管理部品として実装 |
| todo | I4: 所属・未所属一覧、thread read、turn/itemのページ取得が実行を開始しない | new | unit/integration | cursorと公開履歴の境界型 |
| todo | I5: 保存済みGoalのget/clearが実行準備なしで成立し、削除を停止扱いしない | new | unit/integration | 選択対象とのUI結合はM4/M5 |
| todo | I6: 専用homeの起動・認証、管理API永続化、既存の新規実行・停止が製品経路で成立 | new | integration/acceptance | 実接続・認証は模擬試験と区別 |

## 7. 対象ファイル

src/gigas/runtime/codex、services/codex.py、storage/settings.py、対応するtests/unit・tests/integration、docs/codex.md・設定の利用文書。UIの再配置は対象外。

## 8. 検証

各itemのred→green→構造確認とcommitを分ける。対象試験、ruff、ty、全unit/integration、公開APIのbuild、git diff --checkを変更範囲に応じて行う。実app-serverの確認と認証は専用homeで行い、利用者の開発用資格情報をコピーしない。

I1の専用home経路を実装。初回redはCODEX_HOME未指定、レビュー後の追加redはhome未作成。`uv run pytest tests/integration/test_codex_home.py tests/integration/test_codex_service.py tests/integration/test_app_entry.py -q`は41 passed。ruff・tyは成功、`uv lock --check`・`uv build`・差分検査は成功。

I1の構造確認はrefactor-skipped。専用homeは既存factoryへpartialで束縛し、作成は既存launchの明示home経路に置いた。サービスへ設定や認証コピーの状態を追加していない。レビュー指摘のhome実在条件を修正し、同じ試験でgreenを確認した。[実runtimeプローブ](assets/probe_home.py)を`uv run python -X utf8 spec/wip/unit_069/assets/probe_home.py --output-root tmp/u08-m1-home-20261114-02 --result spec/wip/unit_069/assets/probe-home-result.json`で実行し、終了コード0。[結果](assets/probe-home-result.json)は専用home作成・login_required・非認証・正常終了・auth.json未作成を示す。I6の本人認証・管理APIの検証は未実施。

I1の全unitは349 passed、全integrationは455 passed。全体ruffではM0の既存調査資材に53件の違反があり、別の資材修正として解消する。I1の製品・テスト・調査資材のruff、全体format、tyは成功。実機入力はM1のAPI確認には不要。M7でUSB Video Device・COM8を使えることはユーザー承認済み。

## 9. 先送り事項

M2〜M7は親仕様の順序に従う。記録先結合・設定適用・再接続・UI・通し受入をM1の完了へ読み替えない。

## 10. 完了チェックリスト

- [x] 合意・範囲・責務と既存コードの境界を確認した
- [ ] I1〜I6を検証し、各cycleの構造確認を記録した
- [ ] 型・docstring・公開docsと実APIの整合を確認した
- [ ] 必要な品質ゲートと実接続・認証の確認を完了した
- [ ] 親仕様・初期設計の進捗を更新した
