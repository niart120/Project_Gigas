# U02 M0 SDK・ランタイム採用版と接続方式 仕様書

## 1. 概要

状態: **complete（調査・不採用判断完了）**。U02の最初の子仕様。2026-11-11に着手・調査を完了した。製品接続方式の変更判断と実装・実接続受入は親仕様に残す。

目的は、配布されたPython SDKと対応ランタイムを確認し、通常ターン、認証、質問・承認、通知、中断を一つの公開接続で扱えるか判断すること。製品実装と実接続の成功を調査結果だけから宣言しない。

| source | 内容 | path |
|---|---|---|
| user request | U02を開始して完遂する。ブランチ作成、親のwip移動、採用版・接続方式の子仕様から進める | 2026-11-11の依頼 |
| roadmap | U01の次にU02 M0を選ぶ | [05](../../initial/05-implementation.md) |
| parent | 工程の受入条件 | [U02](../../wip/unit_007/CODEX_CONNECTION.md) |

利用場面: 実装担当が、公開配布物・API・Windows環境の根拠から接続方式を選ぶ。質問の回答待ちに受信を止める方式や暗黙の承認を製品へ持ち込まない。

## 2. 対象範囲

公開配布版とPython/Windows条件、起動・認証・モデル確認、要求の保留・回答・失効、SDK受信とサービスループの所有、同梱指示配送、後続のGoal/MCP公開経路を調査する。必要な最小の実行調査と再現可能な確認手順を残す。

## 3. 対象外

この子仕様では製品接続部、GUI操作、ゲーム機器、Goal攻略を実装しない。非公開APIの差し替え、SDKと独自処理の二重受信、独自app-server接続は採用しない。

## 4. 関連docsと前提

[02](../../initial/02-runtime.md)、[06](../../initial/06-repository.md)、[07](../../initial/07-verification.md)、[08](../../initial/08-sdk-compatibility.md)、[09](../../initial/09-control-flows.md)、[U01](../../complete/unit_001/APP_STARTUP.md)。

U01の起動・設定・GUIと設定I/Oワーカーは実装済み。認証・モデル接続・サービスループは未実装。初期調査の開発コミットは採用配布版の根拠にしない。M0で接続条件が成立してからM1を具体化する。

## 5. 振る舞い仕様

| 調査対象 | 成立判断 |
|---|---|
| 配布・起動 | 公開配布版、依存runtime、Windows実行形式を固定して再現できる |
| 認証と設定 | 秘密を通常設定へ保存せず、設定値の保存と利用成功を分ける |
| 要求と通知 | 人間の回答待ち中も同じSDK接続で通知・中断応答を受信できる |
| 終了 | 部分起動失敗、回答待ち、通常ターン実行中から所有資源を解放できる設計を持つ |
| 後続 | Goal/MCPの公開経路の有無と実行未確認を各担当工程へ引き継ぐ |

## 6. TDD Test List

製品振る舞い変更はないためTDDはnot applicable。配布物のソース確認、必要な最小実行、設計レビューで検証する。M1以降の詳細テストは接続方式の成立後に作る。

## 7. 設計メモ

### 7.1 採用判断

**`openai-codex==0.167.0` はU02の製品接続に不採用。** 人間の回答を保留すると唯一の受信スレッドが止まり、通知・中断応答の受信要件が成立しない。M1/M2は開始しない。直接接続への変更案は7.5節に示すが、現行設計の対象外なので採用済みとはしない。

| 調査対象 | 確認した事実 |
|---|---|
| SDK候補 | `openai-codex==0.167.0`、2026-11-06公開、Python `>=3.10`。2026-11-11確認時のPyPI最新安定版 |
| 対応runtime | SDK依存は `openai-codex-cli-bin==0.167.0`。Windows x64/ARM64 wheelあり。実行したのはx64のみ |
| 別の公開版 | より新しいprereleaseなし。既存prereleaseは0.1.0b1/b2/b3。開発コミット `039d0f27d56c31913ffc0f2055a3432b1bcb0b06` のclient.pyも配布版と同一hash |
| Windows限定実行 | Windows 10.0.26200 x86_64、Python 3.13.5。固定runtimeでinitialize、未認証account/read、model/list（9件）、close呼出しと調査Pythonの終了を確認 |
| 未確認 | ChatGPTログイン、利用可能モデルによる通常ターン、実runtimeの質問・承認・中断、異常時の全資源終了。未認証model/listはアカウントの利用権の証明ではない |

製品の依存は変更せず、調査環境を無視対象の`tmp/`へ隔離した。既存アカウントの資格情報はコピーせず、空の調査用`CODEX_HOME`を指定した。許容Python 3.12〜3.15全体の実行確認ではない。

配布物のSHA256:

- `openai_codex-0.167.0-py3-none-any.whl`: `753f17a7ad94720698f7182cc9dbb76906b38d21d728bfa87ac44e110493a815`
- wheel内および固定開発コミットの`client.py`: `8452afbd8148ea212c94e8a6727468054b175acb0daf95002c84b66b604d05a6`

根拠: [SDK配布メタデータ](https://pypi.org/pypi/openai-codex/0.167.0/json)、[runtime配布メタデータ](https://pypi.org/pypi/openai-codex-cli-bin/0.167.0/json)、[公式SDK案内](https://learn.chatgpt.com/docs/codex-sdk)。可変の公式案内は提供方針、固定配布物は具体実装の根拠とする。

### 7.2 要求処理の不成立

[固定ソースclient.py](https://github.com/openai/codex/blob/039d0f27d56c31913ffc0f2055a3432b1bcb0b06/sdk/python/src/openai_codex/client.py)の863〜891行では、唯一のstdout readerが要求handlerを同期実行し、戻り値を即返信する。handlerへ渡るのはmethodとparamsで、JSON-RPC要求IDは渡らない。公開された保留・後送APIも確認できなかった。

認証不要の模擬peerから承認要求、その後に通知と別要求の応答を送った。配布SDKの`CodexClient`と`CodexConfig(launch_args_override=...)`を使用し、非公開メソッド差替えなしで次を観測した。

| 観測 | handler保留中 | declineを返した後 |
|---|---|---|
| 後続通知の受信 | false | true |
| ping要求の応答受信 | false | true |

これはSDK受信制約の再現であり、実runtimeの承認試験ではない。中断応答も同じreaderを通るため、GUI回答を同期的に待つ方式では中断応答も読めない、という判断はソースとこの再現に基づく。

[高水準API](https://github.com/openai/codex/blob/039d0f27d56c31913ffc0f2055a3432b1bcb0b06/sdk/python/src/openai_codex/api.py)の`Codex`/`AsyncCodex`にはhandler注入引数がない。[非同期版](https://github.com/openai/codex/blob/039d0f27d56c31913ffc0f2055a3432b1bcb0b06/sdk/python/src/openai_codex/async_client.py)は同期版を`asyncio.to_thread()`から呼ぶ。サービススレッドやasync版へ移すだけでは解決しない。

低水準標準handlerはコマンド・ファイル変更承認に`accept`を返す。高水準のモードは`auto_review`/`deny_all`のみ。自動承認、全拒否、即時空回答、別ターンへの再送は要求応答契約の代用にならない。

### 7.3 後続のAPI入口・公開性

以下は配布ソースでの入口確認であり、実行未確認。`CodexClient`は低水準モジュールからimportできるがトップレベル公開一覧にない。Goal操作はprivate型を返す部分もあり、安定した高水準公開APIとして扱わない。[API参照](https://github.com/openai/codex/blob/039d0f27d56c31913ffc0f2055a3432b1bcb0b06/sdk/python/docs/api-reference.md)と実装の存在を区別する。

| 対象 | 確認した入口と制約 | 担当 |
|---|---|---|
| 通常turn・指示 | `thread_start(developer_instructions=...)`、`Thread.turn/run`、`TurnHandle.stream/interrupt`。`play.md`はthread作成時に別フィールドで配送可能 | M1。読込・空資材・配送失敗も試験 |
| 認証 | `login_chatgpt`、`login_chatgpt_device_code`、`login_api_key`、`account`、login handleのwait/cancel。資格情報はruntime管理 | ChatGPTはM1、APIキー実接続はU06まで。通常設定へ秘密を保存しない |
| モデル | `models()`、`model_list()`、`supported_reasoning_efforts`。全ページ取得のcursorは汎用requestが必要 | M1。設定保存と利用成功を分ける |
| Goal | 低水準set/pause/clear、汎用requestと生成型によるget。`start_goal_operation`はidle永続threadを要求し旧Goalをclearする | U06。既存Goal再開に流用しない |
| Goal→通常turn | Goal登録中のturn開始は拒否。register/unregister/cancelにprivate状態型を使用 | U07。pause、旧turn終了、登録解放は別。解除だけでは旧書込終了を保証しない |
| MCP | `CodexConfig.config_overrides`、threadのconfig、生成型の`config/mcpServer/reload` | U04画像、U05世代失効。reloadだけで旧接続失効とは保証しない。接続再作成を候補に残す |
| close・旧書込 | stdin close→terminate→2秒wait。例外時kill後のwaitなし。reader/stderrは各0.5秒joinで生存結果を返さない | U02終了、U07切替、U09復元。close戻りやFuture取消しを全資源・旧書込終了とみなさない |

### 7.4 維持する所有境界

設定I/OのQtワーカーは維持し、同じファイルの書込主体を増やさない。保存成功済み設定のスナップショットを接続開始時に渡す。appがサービススレッドとasyncioループ、`runtime/codex`が接続・要求ID・通知変換を所有する。GUIへはqueued signal、GUIからはスレッド安全な投入を使う。

要求を接続世代・thread・turn・request IDに対応付け、有効な要求へ一度だけ回答する。turn ID確定前から停止意思を保持し、遅い通知や回答で解除しない。終了取消しはウィンドウを残すだけで、停止済みturnを再開しない。

終了は受付閉鎖・要求失効・turn中断・接続解放・サービス終了を進め、設定I/O終了と合流してから`ApplicationShell.stopped`を発行する。既存`_finish_shutdown`は設定QThreadだけを確認するため、そのまま流用しない。GUIで長いjoinやFuture.resultを呼ばない。

指示は`importlib.resources`で読み、利用者入力と別に配送する。読込・空資材・配送失敗を成功にしない。製品cwdを開発repoから分け、開発用AGENTS.mdの混入を防ぐ。

### 7.5 方式変更の具体案

**提案: 公開app-serverプロトコルへ直接接続する。** runtimeは今回起動できた`0.167.0`を固定候補とし、SDKを通信主体にせず、一つのasyncio subprocess/stdio接続を`runtime/codex`が所有する。[公式プロトコル](https://learn.chatgpt.com/docs/app-server)を用い、readerは振分けだけを行う。回答待ちは要求ID別にreaderの外へ保持し、writerで返信を直列化する。

停止は保留要求を失効させて`turn/interrupt`を送り、終了通知を並行受信する。ローカルで回答を禁止する処理と、runtimeに必要な拒否・取消し応答を返す処理を区別し、要求IDを捨てるだけでruntimeの待機を残さない。要求種別ごとの具体的な応答方式は次の設計で確認する。子プロセスはterminate後も、必要なkill後もwaitして終了を確認する。認証・モデル・指示配送も同じ接続に集約する。二重受信、独自攻略ループ、自動再接続は追加しない。

現行02・08・親仕様は独自直接接続を初期版の対象外としている。この案の採用にはその設計制約を変更する判断が必要。採用後に02・06・08・親を整合させ、次の子仕様でschema、要求失効、部分起動・終了、実接続試験を具体化する。現時点では提案であり未実装・未検証。

現行制約を維持する選択肢は、SDKに保留・後送・取消し・終了確認の公開経路が追加されるまでM1/M2を保留し、配布版更新時に再調査すること。現在の開発版採用、async化、別受信路、非公開差替え、SDK forkでは進めない。

## 8. 対象ファイル

本書、[U02親](../../wip/unit_007/CODEX_CONNECTION.md)、初期設計05・08・README、親仕様への参照元。採用判断が成立するまで製品依存は追加しない。

## 9. 検証

| 確認 | 結果 | 範囲 |
|---|---|---|
| 開始時Git | pass | main、cleanからcodex/u02-codex-connectionを作成 |
| 文書・ソース・配布物調査 | pass | 7節の根拠と固定hash。SDKの不成立を確認 |
| `uv venv tmp/u02-runtime-env --python 3.13` | pass | 調査用Python 3.13.5を作成 |
| `uv pip install --python tmp/u02-runtime-env/Scripts/python.exe openai-codex==0.167.0` | pass | SDK・runtimeとも0.167.0。製品依存変更なし |
| `uv run --no-project --python tmp/u02-runtime-env/Scripts/python.exe python -` | pass | 空の調査用CODEX_HOMEでinitialize、account/read、model/list、closeを実行。未認証、9件、exit 0 |
| `uv run python tmp/sdk-research/probe_reader.py` | pass（制約再現） | 配布wheelと模擬peer。保留中false/false、回答後true/true。製品受入ではない |
| 本書のPythonコード2件を抽出し隔離Pythonで実行 | pass | runtime確認は未認証・9件・正常終了。reader確認は保留中false/false、回答後通知名とtrue。両方exit 0 |
| 設計・ソースのSubagentレビュー | pass | 公開性見出しと要求失効の区別を採用。掲載コードの実行指摘も対応。重大な指摘なし |
| `uv run python -`によるunit重複・ローカルMarkdown参照検査 | pass | 3状態を通して16 unitが一意、spec内276リンクの参照先が存在 |
| `rg -n '\[TODO\]|TBD|xxx|planned/unit_007|wip/unit_016' spec` | pass | 仮テキスト・古いリンクなし。unit_006にある旧作業範囲の履歴表記だけが一致 |
| `git diff --check` | pass | 空白エラーなし |
| 製品実装・GUI・実ランタイム受入 | not run | 方式不成立によりM1/M2未着手 |
| unit/type/build | not applicable | 製品・依存変更なし。文書の根拠にしない |

限定runtime確認は隔離Pythonへ次を標準入力で渡せば再実行できる。

```python
from pathlib import Path
from openai_codex import CodexConfig
from openai_codex.client import CodexClient

root = Path('tmp/u02-runtime-probe').resolve()
home = root / 'home'
home.mkdir(parents=True, exist_ok=True)
with CodexClient(CodexConfig(cwd=str(root), env={'CODEX_HOME': str(home)})) as client:
    print(client.initialize().model_dump(exclude_none=True))
    print('authenticated:', client.account_read().account is not None)
    print('model_count:', len(client.model_list().data))
print('CLOSE_RETURNED')
```

reader再現の最小コードも同じ隔離Pythonへ渡す。以下の待機時間は調査の打切り用で、製品の停止期限ではない。

```python
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from openai_codex import CodexConfig
from openai_codex.client import CodexClient
from pydantic import BaseModel

entered, release = Event(), Event()
peer = '''import json, sys
print(json.dumps({"id":"approval-1","method":"item/commandExecution/requestApproval","params":{}}), flush=True)
print(json.dumps({"method":"probe/afterApproval","params":{}}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "probe/ping":
        print(json.dumps({"id":msg["id"],"result":{"pong":True}}), flush=True)
'''

class Pong(BaseModel):
    pong: bool

def handler(method, params):
    entered.set()
    if not release.wait(5):
        raise TimeoutError('probe release timeout')
    return {'decision': 'decline'}

with CodexClient(CodexConfig(launch_args_override=(sys.executable, '-u', '-c', peer)),
                 approval_handler=handler) as client:
    assert entered.wait(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        notification = pool.submit(client.next_notification)
        response = pool.submit(client.request, 'probe/ping', None, response_model=Pong)
        try:
            assert not release.wait(0.3)
            before = (notification.done(), response.done())
        finally:
            release.set()
        after = (notification.result(timeout=3).method, response.result(timeout=3).pong)
        print(json.dumps({'while_pending': before, 'after_reply': after}))
        assert before == (False, False)
        assert after == ('probe/afterApproval', True)
```

## 10. 先送り事項

M1/M2の実装と実接続検証は、公開要求経路を成立させる方式判断後に再開する。直接接続案を採る場合は次の子仕様で設計変更と試験を所有する。SDK維持の場合は対応配布版の公開を再調査の契機とする。U03は独立して進められるが、今回のU02完遂という依頼から無断で切り替えない。

APIキー実接続、MCP画像・世代、Goal動作は親仕様にある担当工程・期限を維持する。旧書込終了はU07切替・U09復元の依存実装前に再確認する。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [x] 文書調査と製品TDDを区別した
- [x] 候補の不採用根拠と不成立条件、実行結果・未実行理由を記録した
- [x] 根拠・参照・設計レビューを完了した
