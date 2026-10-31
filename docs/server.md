# PNG生成とサーバ運用

[READMEに戻る](../README.md)

## fixtureからPNGを生成

日本語フォント（Noto Sans CJK JPなど）をOSにインストールし、利用環境のフォントファイルを `FONT_PATH` に指定してください。開発環境とPiそれぞれで設定します。CLIはフォントのみ必須で、取得元URLが未設定でも動きます。

```sh
.venv/bin/python -m paperhub.render \
  --strata tests/fixtures/strata_monitor.json \
  --usage tests/fixtures/xangi_usage.json \
  --out out/paper.png --now 1793411985
```

`--now` はUnix秒で、省略時は現在時刻です。出力は960×540、Lモード、16階調（0〜255を17刻み）、ディザなしです。JSON fixtureのrequestsから直近最大10件のdecode棒グラフ、historyから電力スパークラインを作ります。fixtureのrequestsは5件なので棒も5本です。

## READMEの公開サンプル画像

`.env` の `FONT_PATH` に日本語フォントを設定し、リポジトリのルートで実行します。実データや履歴DBは使いません。

```sh
.venv/bin/python -m paperhub.render \
  --strata tests/fixtures/strata_monitor.json \
  --usage tests/fixtures/xangi_usage.json \
  --out docs/images/sample.png --now 1793411985
```

画像はREADMEから相対参照します。公開前チェックで許可するバイナリはこのパスだけです。描画フォントによって文字の形や幅は変わります。

## サーバを起動

`.env` の `STRATA_MONITOR_URL`、`XANGI_USAGE_URL`、`FONT_PATH` を設定します。サーバは3項目の不足、URL、フォント、ポート、タイムゾーンの不正を起動時に検出します。

```sh
.venv/bin/python -m paperhub.server
```

このコマンドがuvicornを起動し、`.env` の `HOST` と `PORT` を反映します。既定は全インターフェースの8090番、`TZ` は `Asia/Tokyo`、データ保存先は `./data` です。表示順の既定は `claude-code,codex,antigravity` です。

```sh
mkdir -p out
curl --fail http://127.0.0.1:8090/healthz
curl --fail http://127.0.0.1:8090/paper.json
curl --fail 'http://127.0.0.1:8090/paper.png?bat=75&rssi=-60' -o out/paper.png
```

上記は同じマシン上から既定ポートを確認する例です。別ポートを設定した場合は合わせてください。

- `/paper.png`: メモリのPNGキャッシュを返します。電池は0〜100の整数、RSSIは−200〜0の整数を受け付けます。受信値はSQLiteに保存し、次のデータ更新時に描画します。電池・RSSIはそれぞれ値の受信まで非表示です。両方届いた場合は「電池 87%  -58dBm」の形式です。
- `/paper.json`: 最後にPNG化した表示用スナップショットを返します。取得元URLやフォントなどの設定は含めません。
- `/healthz`: プロセスの生存と、各取得元の最終成功からの経過秒を返します。一度も成功していない場合はnullです。取得元がオフラインでも `ok` はtrueです。

Strataは30秒、xangiは60秒ごとに独立して取得します。各試行の通信全体を3秒で打ち切り、失敗したら2秒後に1回だけリトライします。片方の取得やリトライ待ちは、他方を止めません。2回とも失敗した場合は前回値を保持し、最終成功から5分以上経った場合だけ「取得失敗 HH:MM」を表示します。未成功ならデータなしです。失敗表示は見出し右側の更新時刻の隣に出し、本文・モデル・グラフの位置は変えません。リトライ成功時は失敗状態を解除します。Strataは最終成功から10分でオフライン表示になります。

各失敗の診断ログには、取得元の種別、試行番号、例外クラス名、例外メッセージ、HTTPステータス、レスポンス先頭200文字を記録します。レスポンスがない通信エラーはステータスがNone、本文が空文字です。JSON不正などHTTP 200の処理失敗も同じ形式で記録します。

SQLiteは `DATA_DIR/history.db` に作成し、WALを使用します。Strataの5指標と全usage windowの使用率を記録し、電力は60分以内の最大120点を描画します。起動時に履歴を読み、7日超の履歴を起動時と1時間ごとに削除します。取得済みJSONのスナップショットは永続化しないので、再起動直後の各エリアは最初の取得までデータなしです。サーバは1プロセスで起動してください。
