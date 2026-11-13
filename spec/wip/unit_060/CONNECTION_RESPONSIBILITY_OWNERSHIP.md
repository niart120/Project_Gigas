# 接続の責務と状態所有の整理 仕様書

## 1. 概要

### 1.1 目的

着手中の子仕様。質問・承認、モデル候補、Windows環境準備、認証・利用枠の状態を、それぞれの操作と寿命を持つ担当へ移す。会話・Goal・開始・終了・公開phaseとtransportの寿命はCodexConnectionへ残す。公開契約を変えない構造整理として進める。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| user request | 巨大クラスを、状態と振る舞いの帰属から分ける | [親仕様5.9](../unit_054/KISS_SIMPLIFICATION.md#59-巨大クラスの責務状態所有を分ける) |
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

各段階をgreen確認後に別commitとする。新しい振る舞いは追加せず、構造だけのredを作らない。全体gateと独立レビュー後、親の実runtime受入へwipで引き渡す。

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

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 質問・承認・回答・失効を維持 | characterization | unit / integration | baseline 310 passed。抽出後のconnection・answer・GUI 219 passed |
| refactor-done | D02 次ターン選択とGoal設定を維持 | characterization | unit / integration | 抽出後6ファイル310 passed。確定snapshot一件とRpcPeerの再公開 |
| green | D03 環境準備の先行通知・受付不明・切断を維持 | characterization | unit / integration | baseline 310 passed。初期取得とsetup completionを所有 |
| green | D04 認証・利用枠・close後の通知抑止を維持 | characterization | unit / integration | baseline 310 passed。ログイン操作のtaskは接続側 |

## 7. 設計メモ

HumanRequestsは同期処理だけを持ち、tokenからHumanRequestとitem IDを一件で保持する。RPC IDの解決は現在の未解決要求を照合し、回答済みIDはturn終端まで別の集合で保持する。

ModelCatalogのproposeは検証済みModelSelectionを返す。接続が必要なthread/settings/updateを成功させてからcommitする。新しい選択値型・選択中flagを作らない。

子からの通知は既存AccountInfo/LoginEvent/UsageEvent/ModelSelection/SandboxEventの同期事実通知に限る。接続は閉鎖後の通知を捨て、UsageEventの明示falseから唯一の終了要求を作る。認証・モデル・利用枠・準備の子はphaseを保持しない。

Connection.closeの同期部で子の背景taskとsetup completionを取り消す。既存cleanupで操作・通知消費・終了処理と子の背景taskを回収し、transportをfinallyで閉じる。子に第二のclose taskを作らない。open/loginのawait後は接続側でclosedを確認してからusage取得を始め、close受付後の再生成を防ぐ。

子の不正値は局所ValueError、I/OはTransportError/RpcErrorを用い、公開CodexConnectionErrorとfailure IDは接続で扱う。用途固有の受付不明・準備失敗・秘密値拒否を維持し、共有例外moduleを追加しない。

D01は`ab74e25`で記録した。独立レビューでは所属/end判定後の受付、invalid IDの消費、回答検証後の消費、file承認の一件ずつの失効とRPC順に阻害指摘なし。候補tupleを返すdocstringを実体に合わせた。D02は候補・model・effortをModelSelection一件へ寄せ、設定RPCの後だけcommitする。RpcPeerはtransportへ移し、従来のconnectionからのimportを明示的な再公開で維持した。

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
| `spec/wip/unit_060/CONNECTION_RESPONSIBILITY_OWNERSHIP.md` | new | 検証と判断 |

## 9. 検証

計画: 各段階の関係試験と静的検査、最終6ファイル回帰、ruff・ty・lock・build・diff、source/docstring/docsの意味確認と独立レビューを行う。実runtimeの受入は親担当が実施し、模擬試験と区別して記録する。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-u060-baseline --tb=short` | 310 passed / 15.13s | 製品変更前の基準 |
| `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-u060-human --tb=short` | 219 passed | D01。秘密値・承認内容・同turn重複・回答配送・GUI |
| D01対象2ファイルの`ruff check` / `ruff format --check`、`uv run ty check --no-progress`、`git diff --check` | passed | 要求担当の抽出。JSON field不正をValueErrorへ揃える2箇所はTRY004の局所説明を付記 |
| baselineと同じ6ファイル、`--basetemp=tmp/pytest-u060-catalog` | 310 passed / 15.06s | D02。選択・Goal設定・transport公開境界を含む |
| D02対象3ファイルの`ruff check` / `ruff format --check`、`uv run ty check --no-progress` | passed | 新snapshot所有とProtocolの移動。ignore/cast追加なし |
| 段階別・最終gate | not run | 実装後に実行 |
| 実runtime | not run | 親の受入待ち |

## 10. 先送り事項

M2の保存・入力契約は[親仕様](../unit_054/KISS_SIMPLIFICATION.md)に残す。この子は初期契約や公開利用形態を追加しない。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [x] TDD Test Listを更新した
- [ ] 検証結果または未実行理由を記録した
- [ ] 公開API・型・package gateを記録した
- [ ] 独立レビューと親の実runtime受入へ引き渡した
