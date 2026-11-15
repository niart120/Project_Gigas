# 設定・会話・ゲームI/O記録の保存

GIGASの設定、Codexが管理する会話、ゲーム中の作業資料、ゲームI/O記録は、それぞれ異なる所有者と保存先を持つ。

## 配置と所有

| データ | 保存先 | 所有者・用途 |
|---|---|---|
| アプリ設定 | `%LOCALAPPDATA%/GIGAS/settings.toml` | GIGAS。機器・表示・エージェントの設定 |
| Project・会話・標準自動記憶・認証 | 設定ファイルと同じ親フォルダーの`codex_home/` | Codex App Server。GIGASは公開APIで操作・参照する |
| Projectの攻略資料 | Projectに登録した作業フォルダー | エージェントと利用者が編集する自由形式の資料 |
| 未所属会話の作業資料 | 設定ファイルと同じ親フォルダーの`unassigned/<UUID>/` | 新しい未所属会話ごとにGIGASが作業フォルダーを用意する |
| ゲームI/Oと本文 | 各作業フォルダーの`records/<thread-id>/` | GIGASが会話に対応する記録先へ追記する |

Projectは作業資料を共有する単位で、会話はCodexのthreadに対応する。複数の会話が同じProjectの資料を使っても、ゲームI/Oの記録先はthreadごとに分かれる。既存会話の再開には、保存された作業フォルダーを使う。

Codex専用homeの内部形式はapp-serverが管理する。GIGASのゲームI/O記録からCodexの会話を再構成するものではない。

## Projectの登録変更

Projectのフォルダー変更は登録rootと所属チャットのcwdを公開APIで更新する。旧ファイルは移動せず、新しい明示実行は`新root/records/<同じthread-id>/`へ追記する。登録削除ではチャットは保存済みcwdを保持して未所属になる。GIGASは更新進捗の永続台帳を作らず、再起動後も公開metadataから不一致を判定する。詳細は[プロジェクト管理仕様](../functions/projects.md)を参照する。

## アプリ設定

設定はUTF-8のTOMLで保存し、`capture`、`observation`、`controller.serial`、`agent`、`gui`、`project`の各テーブルを使う。ファイルがなければ既定値で開始する。既存ファイルの構文や値が不正な場合はエラーを返す。

保存時は既存TOMLを読み、対象項目を更新して同じフォルダーの一時ファイルへ書き込んでから置き換える。未知の項目やコメントは保持する。設定の保存と、機器・画面への適用は別の結果として扱う。項目と既定値は[設定ガイド](../../guides/settings.md)に記載する。

## ゲームI/Oの記録先

```text
作業フォルダー/
  records/
    <thread-id>/
      .gigas.lock
      events.jsonl
      observations/
        <observation-id>.png
```

実際のthread IDを取得して記録先を開き、通常送信・Goal実行とゲームI/Oへ結び付ける。記録先の選択と寿命は会話側が管理し、保存部品の`GameIoRecordWriter`は渡されたフォルダー内の書き込みを担当する。

既存の記録先は追記用に開く。作業フォルダーが存在すれば不足する記録用ファイル・フォルダーを作成するが、作業フォルダー自体が欠けている場合は実行前にエラーを返す。

## イベントと画像の対応

`events.jsonl`はUTF-8のJSONを1行ずつ追記する。各行は`schema_version`、書込時刻`recorded_at`、イベントの`type`、内容の`payload`を持つ。

| type | 内容・対応付け |
|---|---|
| `dialogue.submitted` | 利用者が提出した本文。受付成功を表す記録ではない |
| `dialogue.message` | Codexの確定message本文。途中の進捗報告も含む |
| `input.operation_started` | 受け付けた操作列と`operation_id` |
| `input.operation_finished` | 同じ`operation_id`に対する入力結果。観測できた場合は`observation_id`を含む |
| `observation.recorded` | `observation_id`、画像の相対パス、取得時刻、元画像と出力画像の寸法 |

画像はMCPへ返したPNGと同じバイト列を保存する。観測IDは観測ごとに作られるため、元フレームが同じでもIDは異なる。`captured_at`はPC側のフレーム取得時刻であり、書込時刻と区別する。

このJSONLの本文保存は、利用者の提出本文とCodexの確定messageに限定している。画面に表示する公開推論要約、計画、ツール活動、質問回答、承認、Goal状態をすべて記録する形式ではない。会話履歴の参照元はCodexである。

項目の詳細とPython APIは[記録付き観測API](recorded-observation.md)に記載する。

## 書き込みと失敗

`RecordingService`が一つのwriterへの画像・入力・本文の保存を直列化する。観測結果の返却や入力・停止処理は保存完了を待たず、保存失敗は通知・ログで伝える。保存に失敗しても、返却済みの画像や完了済みの入力結果は変えない。

PNGを一時ファイルから確定した後、観測行をJSONLへ追記する。両者は一括更新ではないため、JSONLの追記失敗時には画像だけが残ることがある。既存の行・画像の検査や自動修復は行わず、新しい記録は続けて受け付ける。

`.gigas.lock`のOSロックで同じ保存先を使うwriter同士を排他する。切断・終了時は受付を閉じ、投入済みの符号化・保存を回収してからwriterを閉じる。保持期限・容量上限・自動削除はなく、利用者が整理する場合は書き込みを終了してから扱う。

## 実装の入口

- [設定保存](../../../src/gigas/storage/settings.py): TOMLの読込・検証・置換。
- [作業場所の解決](../../../src/gigas/services/_workspaces.py)と[会話資源](../../../src/gigas/services/_conversation_resources.py): 作業場所とthreadごとの記録先。
- [記録writer](../../../src/gigas/storage/records.py)と[保存サービス](../../../src/gigas/services/recording.py): ファイル形式、排他、非同期保存。
