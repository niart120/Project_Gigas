# Bluetooth入力の事前調査・導入計画

## 1. 目的・状態

状態は **planned（BT導入未着手）**。U05 M0で確認したNyXPyとswbt-pythonの調査結果を、将来のBT対応の起点として保持する独立した作業仕様である。U05の実装子仕様ではなく、BT対応をU05の完了条件へ加えない。

利用場面は、専用USB Bluetoothアダプタからゲーム機へ入力し、Gigasの有限操作・停止・事後観測を使うこと。調査結果の記録は完了しているが、GigasでのBT設計確定・実装・実機検証は未実施である。

## 2. 対象範囲・対象外

導入候補はswbt-pythonによる入力接続。接続設定、ペアリング情報、完全入力状態の変換、対応入力の公開、停止と終了、依存・配布への影響を扱う。

現在の作業は事前調査の記録だけとする。依存追加、BT用UI・adapterの先行実装、詳細TDD、実機操作、PR作成は対象外。Joy-ConのSL/SRやIMUへの入力表現拡張は、BT通信の追加と分けて採用範囲を決める。

## 3. 関連仕様・調査対象

- [U05親仕様](../../wip/unit_010/INPUT_AND_STOP.md)
- [U05 M0](../../complete/unit_036/INPUT_AND_STOP_DESIGN.md)
- [ゲームI/O設計](../../initial/03-game-io.md)

調査日は2026-11-12。NyXはローカル `E:/documents/VSCodeWorkspace/Project_NyX` のcommit `648f860e5afd582f80aa58645570a16bfc68a393`、swbt-pythonはGitHubの0.6.0を示すmainの公開API・依存定義・実装を参照した。mainリンクは可変のため、導入時に採用版を再確認する。PRの実機結果は既存記録の参照であり、Gigasでの実行結果ではない。

## 4. 確認した事実

| 対象 | 結果・根拠 |
|---|---|
| 接続方式 | swbtは専用USB BluetoothドングルをBumbleから使う。WindowsではWinUSB等の準備が必要。列挙対象はドングルでありゲーム機一覧ではない。[機器準備](https://github.com/niart120/swbt-python/blob/main/docs/hardware.md) |
| 公開API | DirectProController / DirectJoyConL / DirectJoyConRに、非同期のsend(InputState)、neutral、接続・終了APIがある。プロトコル初期化と応答処理はライブラリが所有する。[API](https://github.com/niart120/swbt-python/blob/main/docs/api.md)、[実装](https://github.com/niart120/swbt-python/blob/main/src/swbt/gamepad/interface.py) |
| 完全状態の送信 | NyXのsession.applyはDirect型のsendへ委譲する。状態更新だけの周期送信とは完了条件が異なる。送信API成功はゲーム画面への反映保証ではない。[NyX #199](https://github.com/niart120/Project_NyX/pull/199)、[契約](https://github.com/niart120/Project_NyX/issues/196) |
| 非同期処理と所有 | NyXは同期ControllerOutputPortのために専用asyncioスレッドへ橋渡しする。統合時の修正対象は非同期API、座標変換、GUI処理、終了処理、古い入力ポート・dummyの残存。[#191](https://github.com/niart120/Project_NyX/pull/191)、[#190](https://github.com/niart120/Project_NyX/pull/190) |
| 接続情報 | swbtのprofile_pathはペアリング情報を保持する。コントローラー形状・接続先ごとに分ける。NyXのUIはペアリングと接続を区別し、プロファイルのパス選択を通常UIから除いている。[API](https://github.com/niart120/swbt-python/blob/main/docs/api.md)、[NyX #202](https://github.com/niart120/Project_NyX/pull/202) |
| Python・依存 | swbt-python 0.6.0はPython >=3.13、Bumble 0.0.233。Gigasは現在Python >=3.12。通常依存として採用する場合は対応Python・lock・CIの変更が必要。[依存定義](https://github.com/niart120/swbt-python/blob/main/pyproject.toml)、[NyX #201](https://github.com/niart120/Project_NyX/pull/201) |

NyXのコード読取対象は `src/nyxpy/framework/core/hardware/swbt/` のconfig、mapper、controller、sessionと、`tests/unit/framework/hardware/swbt/test_session.py`。完全状態からの変換、スティックY軸反転、対応入力の制限、send/neutral/closeの呼出し、取消し後の終了処理を確認した。テストの存在と今回のテスト実行成功は区別する。

NyXの実機記録は、swbt 0.5.4・CSR8510 A10・Switch 2でJoy-Con L/RのSL/SR、Pro Controllerの右上方向、16/33/50msのA短押し各5回、GUI手動操作を確認している。短押しの一般保証ではない。0.6.0更新時のNyX実機再検証は未実施。[実機結果](https://github.com/niart120/Project_NyX/pull/200)、[版更新時の限界](https://github.com/niart120/Project_NyX/pull/201)

## 5. Gigasへの適用案と影響度

以下は設計上の見積もりであり、採用決定や工数保証ではない。

| 範囲 | 見積もり |
|---|---|
| 接続・入力 | 中。既存サービスループからswbtの非同期APIを呼ぶadapterと、完全入力状態の変換を追加する。NyXの同期橋渡し・接続cache・dummyは移植しない |
| 設定・UI・プロファイル | 中。接続方式、アダプタ、コントローラー種別、初回登録と保存済み接続の扱いを追加する。通常接続のOK／適用という方針を前提に初回登録の導線を決める |
| MCP | 小〜中。対応入力をツール説明へ反映し、次回送信で公開を更新する既存契約を使う |
| 操作・観測・保存 | 小。有限列、取消し、終了時解除、事後画像、非同期保存を再利用する。BT固有の送信中停止・通信断は別途確認する |
| 依存・配布 | Pythonの下限、依存・lock・CI、ドングル準備手順に変更が必要 |

共通実行部は入力状態を扱い、port・baud・byte数・シリアル専用workerをBTへ強制しない。押下時間と取消しはGigasが所有し、BT側は完全状態のsendとneutralを使う案が適合する。将来用の汎用plugin機構や空のBT設定は作らない。

対応入力は通信方式と分けて扱う。Joy-Con L/Rには使えないボタン・スティックがある。SL/SR・IMUまで公開するかは導入範囲の決定事項であり、ライブラリに機能があることだけを理由に追加しない。

## 6. 着手条件・未決事項

BT対応が実行対象として選ばれた時点でwipへ移し、採用版とその時点のU05実装を照合する。実装前に以下を確定する。

| 事項 | 判断する時点 |
|---|---|
| 対象コントローラー、ゲーム機、専用ドングル、採用swbt版、対応Python | BT設計開始時 |
| 初回ペアリングと再接続のUI、プロファイル保存先・寿命 | 設定・接続実装前 |
| 接続・送信中の取消し、neutralとcloseの結果、再接続 | adapter設計時。実機での解除確認は公開前 |
| SL/SR・IMUの要否、座標変換、能力のツール説明 | 入力表現・MCP実装前 |

内部の変換と模擬接続試験は実機なしで進められる。初回登録、保存済み接続、入力反映、停止・終了の実機確認をBT受入の条件とする。詳細な分割と子仕様は着手時に必要な範囲だけ作る。

## 7. TDD・対象ファイル

事前調査文書のためred/greenはnot applicable。実装時は既存の設定適用、操作取消し、MCP能力説明の試験を改訂し、BT変換・接続の試験だけを補う。詳細Test Listは未作成。

実装対象候補は `game_io/input/`、設定保存・設定UI、入力サービス、MCP説明、対応テスト、pyproject.toml・uv.lock・CI・利用手順。現時点ではいずれも変更しない。

## 8. 検証・完了条件

| 検証 | 結果 |
|---|---|
| GitHubのrepository検索、PR・issue・公開API・実装の読取、NyXローカルのコード・テスト読取 | 確認済み。根拠と版の限界は4節 |
| `uv run --no-sync python -X utf8 tmp/validate-u05-docs.py` / `git diff --check` | pass。ローカルリンク・unit配置等と空白を検査。外部URL・意味の正確さは読取で確認 |
| Python lint・型検査・pytest・build | not applicable。文書のみの変更 |
| GigasへのBT導入・実接続・実機操作、NyXテストの再実行 | not run |

- [x] 事前調査の事実・提案・未確認事項を分けた
- [ ] 採用範囲と未決事項を確定した
- [ ] 必要な実装・自動試験を完了した
- [ ] 採用版と対象機器で実機受入を完了した

本仕様のcomplete移動はBT導入の受入後とし、調査記録の作成だけでは完了扱いにしない。
