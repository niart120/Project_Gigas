# U05 最新キャッシュ観測への統一

## 目的・状態

状態: complete。ユーザーが決定した最新キャッシュ観測を、実装と初期仕様へ反映する。

## 対象範囲・関連文書

[U05親](../../complete/unit_010/INPUT_AND_STOP.md)、[ゲームI/O](../../initial/03-game-io.md)、[M0](../../complete/unit_036/INPUT_AND_STOP_DESIGN.md)に従う。既存M1実装と試験を改訂する。

## 対象外

M2公開、実機検証、Bluetooth。

## 振る舞い仕様

操作列の最後の解除・末尾waitが完了した後に、単独observeと同じ処理で最新キャッシュを一度取得する。新しいFrameを待たず、操作前や前回観測と同じFrameも許容する。画像取得時刻やゲームへの入力反映は保証しない。

read開始時計、境界時刻、操作受付世代との照合、フレーム待機・期限・期限切れエラーを撤去する。Frameの取得完了時刻・世代・連番は取得側で維持する。単独観測の取消しと保存の寿命は維持する。入力枠は列と解除の終了時に解放し、直後に最新Frameを選ぶ。符号化中は次の入力を許可する。停止は観測応答を取り消し、開始済みの符号化・保存は観測側で継続する。停止時に観測IDの完成を待たず、後から操作記録へ追記しない。

## TDD Test List

| 状態 | 振る舞い | 検証 |
|---|---|---|
| green | 操作前から更新されないキャッシュでも列完了後の観測が成功する | 既存入力結合試験を改訂。変更前はOBSERVATION_TIMEOUTで期待失敗 |
| green | 取得時計・共有搬送は取得完了時刻を維持する | 既存capture試験を改訂 |
| green | 停止中の符号化結果と観測IDを残し、保存完了を待たない | 既存入力停止・観測保存試験を維持 |

## 対象ファイル

captureモデル・backend・共有搬送、観測・入力サービス、既存unit/integration試験、初期03/07・M0・親・API文書。

## 検証

- `uv run pytest tests/unit tests/integration -q`: 593 passed。入力・観測・保存・captureと既存GUI/MCPの回帰。
- `uv run pytest tests/integration/test_input_service.py tests/integration/test_input_observation_path.py -q`: 最終改訂後22 passed。現在の取得世代の採用と繰り返し停止を確認。
- `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress`: pass。Frame・observe公開APIの型、docstring、書式を確認。新しい型抽象化・ignore・castなし。
- `uv lock --check` / `uv build`: pass。依存整合とsdist/wheel生成。依存追加なし。
- `git diff --check`: pass。差分の空白検査。

差分レビューでは画像選定、操作終了と観測取消しの所有、共有メモリのpack/unpack、会話ID引継ぎを確認した。旧鮮度判定専用テストを撤去し、取消し・保存の試験は既存入力/観測試験へ統合した。画像更新を待たないため同じFrameが返り得る。実機の入力反映はnot run。MCP入力公開とGUI結合はM2の範囲で未実施。

## 後続契約

入力・観測・保存の停止と失敗の扱いは[責務分離](../unit_041/INPUT_FAILURE_SEPARATION.md)に従う。

## 完了条件

- [x] 既存試験でキャッシュ再利用を確認
- [x] 仕様・API・全体gateと差分レビューを完了
