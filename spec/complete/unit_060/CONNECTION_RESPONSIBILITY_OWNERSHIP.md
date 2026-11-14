# 接続の責務と状態所有の整理 仕様書

## 1. 概要

### 1.1 目的

完了した子仕様。質問・承認、モデル候補、Windows環境準備、認証・利用枠の状態を、それぞれの操作と寿命を持つ担当へ移した。会話・Goal・開始・終了・公開phaseとtransportの寿命はCodexConnectionへ残した。構造整理後に、到達が確認された初期化中の利用枠通知をD05で修正した。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| user request | 巨大クラスを、状態と振る舞いの帰属から分ける | [親仕様5.9](../../wip/unit_054/KISS_SIMPLIFICATION.md#59-巨大クラスの責務状態所有を分ける) |
| preceding unit | サービスの資源と入力runの所有を分離 | [unit059](../../complete/unit_059/SERVICE_RESPONSIBILITY_OWNERSHIP.md) |
| adopted contract | 通知寿命と単一の終了所有 | [unit056](../../complete/unit_056/TURN_NOTIFICATION_LIFETIME.md)、[unit057](../../complete/unit_057/TURN_END_OWNERSHIP.md) |

### 1.3 use case

| actor / boundary | 入力または状態 | 期待する観測結果 | 制約 |
|---|---|---|---|
| 利用者 | 質問への回答、承認判断、モデル選択 | 同じ回答・配送結果と次ターン設定 | 受付不明の自動再送なし |
| runtime | ログイン完了、利用枠更新、環境準備完了 | 現在の通知と受付制限 | phase・終了要求は接続だけが決定 |
| 終了 | close中に先行操作の応答が到着 | 所有taskを回収し、遅い完了を公開しない | OSの環境準備自体の取消しは保証しない |

## 2. 対象範囲

- HumanRequests、ModelCatalog、WindowsSandbox、AccountSessionへの状態と操作の移動。
- 要求とitemの一記録化、逆引き辞書2個の削除、ModelSelection一件による選択の保持。
- RpcPeerの自然なtransport境界への移動とconnectionからの再公開。
- 背景taskの単独所有、例外・事実通知・closeの境界と既存試験の整合。

## 3. 対象外

- M2の保存・入力契約、GUIの新機能、Goalやphaseの第二の状態機械。
- 共有errors module、汎用manager、event bus、mixin、親内部状態を照会するcallback群。
- 新しい停止期限、操作の再送、任意故障注入からの防御契約追加。

## 4. 関連 docs

- [runtime正本](../../initial/02-runtime.md)
- [SDK境界](../../initial/08-sdk-compatibility.md)
- [制御フロー](../../initial/09-control-flows.md)
- [公開Codex API](../../../docs/codex.md)

### 4.1 前提・着手条件

unit059は`ef469a4`でcompleteへ移動済み。開始時branchは`codex/kiss-simplification-spec`、worktreeはclean、3状態の最大unit番号は059だった。親unit054は親担当だけが編集する。

### 4.2 冒頭の調査・設計

既存6ファイルの310試験を変更前に再実行してgreenを確認した。子の背景taskはConnection._operationsへ二重登録しない。公開操作のtaskと通知消費は接続、利用枠取得はAccountSession、初期readiness取得はWindowsSandboxが所有する。子から接続のstop/closeをawaitしない。

### 4.3 工程内の分割

1. D01: 質問・承認・回答の既存振る舞いを保ち、要求の所有を分ける。
2. D02: モデル候補と選択を既存snapshotにまとめる。
3. D03: 環境準備一件と初期readiness取得を分ける。
4. D04: 認証・ログイン相関・利用枠を分ける。
5. D05: 起動途中の利用枠通知で終了phaseへ遷移せず、起動完了時の全体取得で利用禁止を反映する。

各段階をgreen確認後に別commitとする。構造だけのredを作らず、D05だけは到達する起動順序を代表一件でred→greenにする。全体gateと独立レビュー後、親の実runtime受入へwipで引き渡す。

## 5. 振る舞い仕様

| 振る舞い | 入力・状態 | 期待結果 | 備考 |
|---|---|---|---|
| 要求寿命 | 現turnの要求、回答、終端 | 同turnの重複抑止、回答前の検証、終端で破棄 | 所属・終了判定は接続 |
| file承認 | 提示済みpatchが変化 | 一件ずつresolve、通知、RPC拒否 | 全件を先に消費しない |
| モデル選択 | retained Goalあり | 設定RPC成功後にsnapshotを確定 | 失敗なら旧選択を保持 |
| ログイン | 完了通知が開始応答より先行 | login IDを照合し、古いURLを出さない | 先行通知は開始中だけ保持 |
| 利用枠 | 疎な通知、取得失敗、明示false | 全体再取得を集約し、既知falseを保持。接続が停止を決定 | unknownから許可を推測しない |
| 環境準備 | 開始受付不明、先行完了 | 再送せず完了とreadinessを待つ | 明示拒否と受付不明を区別 |
| close | 背景取得・setup・loginが保留 | 同期に取消しを要求し、所有者が回収 | close後にusage taskを再生成しない |
| 起動途中の利用枠通知 | account確認済み・catalog応答待ち | 接続完了前は利用枠取得を始めず、初期取得をopen末尾で行う | connectingの間にstopping/error/stoppedを発行しない |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 質問・承認・回答・失効を維持 | characterization | unit / integration | baseline 310 passed。抽出後のconnection・answer・GUI 219 passed |
| refactor-done | D02 次ターン選択とGoal設定を維持 | characterization | unit / integration | 抽出後6ファイル310 passed。確定snapshot一件とRpcPeerの再公開 |
| refactor-done | D03 環境準備の先行通知・受付不明・切断を維持 | characterization | unit / integration | 抽出後6ファイル310 passed。初期取得とsetup completionを所有 |
| refactor-done | D04 認証・利用枠・close後の通知抑止を維持 | characterization | unit / integration | 抽出後6ファイル310 passed、全体846 passed。ログイン操作のtaskは接続側 |
| refactor-skipped | D05 起動途中の利用枠通知を初期取得へまとめる | regression | unit | connectingがstoppingになるred→起動条件を一か所変更してgreen。D04で所有を分離済みのため追加構造変更なし |

## 7. 設計メモ

HumanRequestsは同期処理だけを持ち、tokenからHumanRequestとitem IDを一件で保持する。RPC IDの解決は現在の未解決要求を照合し、回答済みIDはturn終端まで別の集合で保持する。

ModelCatalogのproposeは検証済みModelSelectionを返す。接続が必要なthread/settings/updateを成功させてからcommitする。新しい選択値型・選択中flagを作らない。

子からの通知は既存AccountInfo/LoginEvent/UsageEvent/ModelSelection/SandboxEventの同期事実通知に限る。接続は閉鎖後の通知を捨て、UsageEventの明示falseから唯一の終了要求を作る。認証・モデル・利用枠・準備の子はphaseを保持しない。

Connection.closeの同期部で子の背景taskとsetup completionを取り消す。既存cleanupで操作・通知消費・終了処理と子の背景taskを回収し、transportをfinallyで閉じる。子に第二のclose taskを作らない。open/loginのawait後は接続側でclosedを確認してからusage取得を始め、close受付後の再生成を防ぐ。

子の不正値は局所ValueError、I/OはTransportError/RpcErrorを用い、公開CodexConnectionErrorとfailure IDは接続で扱う。用途固有の受付不明・準備失敗・秘密値拒否を維持し、共有例外moduleを追加しない。

D01は`ab74e25`で記録した。独立レビューでは所属/end判定後の受付、invalid IDの消費、回答検証後の消費、file承認の一件ずつの失効とRPC順に阻害指摘なし。候補tupleを返すdocstringを実体に合わせた。D02は候補・model・effortをModelSelection一件へ寄せ、設定RPCの後だけcommitする。RpcPeerはtransportへ移し、従来のconnectionからのimportを明示的な再公開で維持した。

D02は`b4dcdb5`で記録し、独立レビューでもcatalog取得拒否、無効effort、設定RPC後のcommit、公開Protocol再公開に阻害指摘なし。D03は環境準備moduleと接続配線を別担当で実装・相互確認した。setup completionをclose同期部で失効し、各await後に同じcompletionが有効か確認する。子にclosed状態や接続状態を読むcallbackは追加していない。初期read待機後に停止済みなら、readiness不備を新しい準備失敗として報告せず従来どおり中止する。

D03は`59d5be5`で記録した。親の独立レビューでもmoduleと配線の阻害指摘なし。指摘に従い、cancel_pending済みのreadをcloseで再cancelする分岐と、通常例外を返さない子closeへの重複したfinallyを除いた。

D04は先行login完了の保持をJSON全体から`dict[str, bool]`へ絞り、秘密のremote errorを保持しない。FalseとNoneを区別し、既存の失敗通知・相関を維持する。親だけがclosedを確認してusage取得を始め、子は事実を同期通知する。UsageEventの明示falseから接続が同期に終了要求を作るため、子がstop/closeをawaitして自分の回収を待つ経路を持たない。

D04は`337c291`で構造commitした。最終確認で、pump開始→account確認→catalog待機中の利用枠通知という順序を見つけた。旧stopの未接続例外はこの経路で到達し得るため、「到達不能」という説明を撤回した。D05はrefreshの受付をopenedかつnot closedへ限定し、既存のopen末尾の初期取得へ合流する。新しい保留状態・再送・例外回復を追加しない。

D05の親レビューでは、内部flagを直接操作せずcatalog保留と実通知から再現し、公開phase・RPC・利用禁止後の送信拒否を確認する試験と、一か所の受付条件変更に追加指摘なし。D01〜D04の構造greenとD05のred/greenを別の変更として記録した。

独立レビューではD04の先行完了、子背景taskの単独所有、同期取消しとclosed後の再起動抑止、phase・終了要求の唯一所有、既存login取消しに阻害指摘なし。子と接続の相互参照、外部からの子private属性の読み書き、追加のignore/castはない。

構造の補助測定では`ef469a4`からCodexConnectionのclass範囲が1524→1215行、直下method数が60→54、初期属性が43→29へ変わった。これは子へ移した状態を含む総状態量が同数減ったという意味ではない。具体的な削減は要求の逆引き辞書2個、選択の独立属性3個から既存snapshot一件、login先行通知のraw payload保持である。背景taskの回収は共有task集合から各子へ限定し、各責務の状態を直接読み書きする箇所を接続本体から除いた。

## 8. 対象ファイル

| path | change | 内容 |
|---|---|---|
| `src/gigas/runtime/codex/connection.py` | modify | 所有者の組立、公開境界とphase |
| `src/gigas/runtime/codex/_human_requests.py` | new | 現turnの要求 |
| `src/gigas/runtime/codex/_model_catalog.py` | new | 候補と選択snapshot |
| `src/gigas/runtime/codex/_windows_sandbox.py` | new | readinessとsetup |
| `src/gigas/runtime/codex/_account_session.py` | new | 認証・login・利用枠 |
| `src/gigas/runtime/codex/transport.py` | modify | RpcPeer定義 |
| `tests/unit/test_codex_connection.py`、関連integration | modify if needed | 公開動作による回帰確認 |
| `spec/complete/unit_060/CONNECTION_RESPONSIBILITY_OWNERSHIP.md` | new | 検証と判断 |

## 9. 検証

計画: 各段階の関係試験と静的検査、最終6ファイル回帰、ruff・ty・lock・build・diff、source/docstring/docsの意味確認と独立レビューを行う。実runtimeの受入は親担当が実施し、模擬試験と区別して記録する。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-u060-baseline --tb=short` | 310 passed / 15.13s | 製品変更前の基準 |
| `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-u060-human --tb=short` | 219 passed | D01。秘密値・承認内容・同turn重複・回答配送・GUI |
| D01対象2ファイルの`ruff check` / `ruff format --check`、`uv run ty check --no-progress`、`git diff --check` | passed | 要求担当の抽出。JSON field不正をValueErrorへ揃える2箇所はTRY004の局所説明を付記 |
| baselineと同じ6ファイル、`--basetemp=tmp/pytest-u060-catalog` | 310 passed / 15.06s | D02。選択・Goal設定・transport公開境界を含む |
| D02対象3ファイルの`ruff check` / `ruff format --check`、`uv run ty check --no-progress` | passed | 新snapshot所有とProtocolの移動。ignore/cast追加なし |
| baselineと同じ6ファイル、`--basetemp=tmp/pytest-u060-sandbox` | 310 passed / 15.08s | D03。準備受付不明、先行完了、切断中readiness、製品準備 |
| D03対象2ファイルの`ruff check` / `ruff format --check`、`uv run ty check --no-progress` | passed | 子背景taskの所有、公開例外への変換、事実通知 |
| baselineと同じ6ファイル、`--basetemp=tmp/pytest-u060-account` | 310 passed / 15.24s | D04。認証・usage・close・GUI配送の回帰 |
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u060-full --tb=short` | 846 passed / 61.80s | D04終了時の全unit/integration |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | passed | Python228ファイル、D/ANNを含む形式・型境界 |
| `uv lock --check` / `uv build` / `git diff --check` | passed | lock整合、sdist/wheel生成、差分空白 |
| `uv run python -X utf8 tmp/u054/validate_docs.py ef469a4` | 4 documents / 45 local_links / 60 units、errors=0 | 子仕様と同時作業中の親文書の参照先・仮テキスト検査 |
| source/docstring/docs review | 確認済み | 公開操作・例外・RpcPeer再公開を維持。親の公開docs訂正と現在の製品準備・停止受付の契約を照合 |
| `uv run pytest tests/unit/test_codex_connection.py::test_usage_notifications_during_open_wait_for_initial_read -q --basetemp=tmp/pytest-u060-open-usage-red --tb=short` | 1 failed、期待したred | catalog保留中に利用枠通知を受け、connecting期待がstoppingになる |
| 同じ試験、`--basetemp=tmp/pytest-u060-open-usage-green` | 1 passed / 0.06s | openedかつnot closedだけで取得を開始。初期falseと送信拒否を確認 |
| baselineと同じ6ファイル、`--basetemp=tmp/pytest-u060-final` | 311 passed / 16.42s | D05後の関連回帰 |
| D05後のrepo `ruff format --check .` / `ruff check .` / `ty check --no-progress` / `uv lock --check` / `uv build` / `git diff --check` | passed | 最終sourceの形式・型・lock・配布物・空白 |
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u060-final-full --tb=short` | 847 passed / 62.78s | D05を含む最終全unit/integration |
| 最終の`uv run python -X utf8 tmp/u054/validate_docs.py ef469a4` | 5 documents / 53 local_links / 60 units、errors=0 | 子仕様と親が訂正中の文書を含む。内容レビューとは区別 |
| 実runtime | passed（9.1の修正済み検証器による再実行） | 既存認証・実準備状態・停止回収・同接続再開・自然終端・終端前close |

### 9.1 最終コードの実runtime受入と検証器の訂正

製品の最終変更はD05 `a40edfa`。親担当がD04配線とD05の公開状態・初期取得の回帰を読み、追加独立レビューも阻害指摘なし。D05の試験は合法な通知順序から問題を再現し、内部状態の直接書換えや新しい状態を追加していない。

最初の再検証`tmp/u054/m1-neutral-20261113-233531-642800/report.json`と`m1-close-wait-20261113-233723-295920/report.json`には、`status: passed`と`error: Serial neutral send was not observed`が併存していた。非入力modeにも中立送出観測を要求し、その失敗で先に設定したpassedを戻していなかった検証器の不備である。これらの合格表示は最終受入へ数えない。同じ不備がunit_057時点の2実行にもあったため、原記録を保持して同仕様9.2へ訂正を追記した。実入力modeは押下後の中立を観測しており、該当errorはない。

検証器は非入力modeへの余分なassertを除き、通常例外時に必ずfailedへ戻すよう修正した。input枝の押下後中立・送出回数・途中停止の検査は残す。独立レビューで通常例外、cleanup失敗、不成立の判定を確認した。成功判定はreport単独でなく、プロセス終了コード0、トップレベルerror/errors/cleanup_errorなし、全cleanup成功、所有PID残存0を合わせて確認する。再実行にCtrl+Cや人手介入はなかった。

```powershell
$m1Settings = 'C:/Users/train/AppData/Local/GIGAS/settings.toml'
$m1Home = 'E:/documents/VSCodeWorkspace/Project_Gigas/tmp/u06-m3/fresh-20261113-180214-566284/home'
uv run python -X utf8 tmp/u054/m1_acceptance.py --execute --mode neutral --settings $m1Settings --codex-home $m1Home
uv run python -X utf8 tmp/u054/m1_acceptance.py --execute --mode close-wait --settings $m1Settings --codex-home $m1Home
```

| 最終結果資料（`tmp/u054/`配下） | 観測結果 |
|---|---|
| `m1-neutral-20261113-234151-792876/report.json` | passed・終了コード0・errorなし。stop受付265714.203→終端265714.671→stopped 265714.734。1ページ列挙・1件終了・空再確認。有限書込8行で停止後1秒と同接続/同thread再開後も不変。自然最終応答を受信し、終端265735.250→ready 265735.281、空sweepで再中断なし |
| `m1-close-wait-20261113-234304-913559/report.json` | passed・終了コード0・errorなし。公開stop直後にdisconnect。close入口で対象turn終端は未受信、約0.547秒で終了。有限書込7行で切断後1秒も不変。通知遅延の注入や検証器からのterminateなし |

両実行ともruntime 0.167.0、gpt-6.1-sol / low、既存の認証・準備済み隔離homeで確認した。入力要求・非中立送出は0、service/settings/capture/serialを回収し、shutdown errorなし・所有PID残存0。新規login・sandbox setup・質問や承認の実要求はこの再検証では行っておらず、それらの相関は既存固定試験の確認範囲である。短いCH552実入力の受入は、入力側を変更した[unit_059の9.1](../unit_059/SERVICE_RESPONSIBILITY_OWNERSHIP.md#91-実runtime実機受入)と区別する。

## 10. 先送り事項

M2の保存・入力契約は[親仕様](../../wip/unit_054/KISS_SIMPLIFICATION.md)に残す。この子は初期契約や公開利用形態を追加しない。長時間運転・別機器は今回の受入外。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [x] TDD Test Listを更新した
- [x] 検証結果または未実行理由を記録した
- [x] 公開API・型・package gateを記録した
- [x] 独立レビューと親の実runtime受入へ引き渡した
- [x] 親の実runtime受入と検証器の訂正を記録した
