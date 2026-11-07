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

画像はREADMEから相対参照します。公開前チェックで許可するバイナリはこのパスと、詳細画面のsample-strata.png / sample-usage.pngだけです。描画フォントによって文字の形や幅は変わります。

## 3画面を生成・比較

```sh
for view in dashboard strata usage; do
  .venv/bin/python -m paperhub.render \
    --strata tests/fixtures/strata_monitor.json \
    --usage tests/fixtures/xangi_usage.json \
    --out "out/$view.png" --now 1793411985 --view "$view"
done
```

`--view`省略時はdashboardです。公開用の詳細サンプルを更新する場合は、同じコマンドの出力先を `docs/images/sample-strata.png` / `docs/images/sample-usage.png` にします。3画面とも同じfixture・時刻・フォントで比較してください。

## レイアウト変更前後を比較

同じfixture・時刻・フォントで比較してください。今回の変更前画像はブランチの分岐元の公開サンプルから取り出せます（リポジトリのルートで実行）。

```sh
mkdir -p out
git show main:docs/images/sample.png > out/before.png
.venv/bin/python -m paperhub.render \
  --strata tests/fixtures/strata_monitor.json \
  --usage tests/fixtures/xangi_usage.json \
  --out out/paper.png --now 1793411985
.venv/bin/python - <<'PYCODE'
from PIL import Image
before = Image.open("out/before.png").convert("L")
after = Image.open("out/paper.png").convert("L")
comparison = Image.new("L", (960, 1092), 255)
comparison.paste(before, (0, 0))
comparison.paste(after, (0, 552))
comparison.save("out/comparison.png")
PYCODE
```

比較画像は上が変更前、下が変更後です。`main` に統合した後は、変更前のコミットを `git show` の参照に指定してください。フォントが異なる場合、文字の形・幅も変わります。`out/` の画像はGit管理外です。

## サーバを起動

`.env` の `STRATA_MONITOR_URL`、`XANGI_USAGE_URL`、`FONT_PATH` を設定します。サーバは3項目の不足、URL、フォント、ポート、タイムゾーンの不正を起動時に検出します。Strata で認証を有効にしている場合は、同じ `.env` に `STRATA_API_KEY` を設定してサーバを再起動します。

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

- `/paper.png?view=dashboard|strata|usage`: view別のPNGキャッシュを返します。省略時はdashboard、不正なviewは422です。`X-Paper-View`ヘッダが選んだviewと一致することを確認できます。電池は0〜100の整数、RSSIは−200〜0の整数を受け付けます。受信値はSQLiteに保存し、次のデータ更新時に描画します。電池・RSSIはそれぞれ値の受信まで非表示です。両方届いた場合は「電池 87%  -58dBm」の形式です。
- `/paper.json`: 最新データ更新時に確定した表示用スナップショットを返します。描画専用のdetailsは含めず、従来の返却内容を維持します。取得元URLやフォントなどの設定は含めません。
- `/healthz`: プロセスの生存と、各取得元の最終成功からの経過秒を返します。一度も成功していない場合はnullです。取得元がオフラインでも `ok` はtrueです。

Strataは30秒、xangiは60秒ごとに独立して取得します。各試行の通信全体を3秒で打ち切り、失敗したら2秒後に1回だけリトライします。Strata の HTTP 401/403 はキーとアクセス設定の確認を促すログを出し、次の通常周期まで再取得を待ちます。片方の取得やリトライ待ちは、他方を止めません。その周期の取得が失敗した場合は前回値を保持し、最終成功から5分以上経った場合だけ「取得失敗 HH:MM」を表示します。未成功ならデータなしです。失敗表示は見出し右側の更新時刻の隣に出し、本文・モデル・グラフの位置は変えません。リトライ成功時は失敗状態を解除します。Strataは最終成功から10分でオフライン表示になります。

各失敗の診断ログには、取得元の種別、試行番号、例外クラス名、例外メッセージ、HTTPステータス、レスポンス先頭200文字を記録します。Strata の診断ログでは、設定したキーが例外メッセージやレスポンス本文に含まれる場合は伏せます。レスポンスがない通信エラーはステータスがNone、本文が空文字です。JSON不正などHTTP 200の処理失敗も同じ形式で記録します。

SQLiteは `DATA_DIR/history.db` に作成し、WALを使用します。Strataの5指標と全usage windowの使用率を記録し、電力は60分以内の最大120点を描画します。起動時に履歴を読み、7日超の履歴を起動時と1時間ごとに削除します。取得済みJSONのスナップショットは永続化しないので、再起動直後の各エリアは最初の取得までデータなしです。サーバは1プロセスで起動してください。

受信データを確定する前に、同じsnapshotで3画面を描画して検証します。全画面成功時に生成済みPNGをまとめてキャッシュへ採用し、描画例外では受信データ・履歴・Prefillを巻き戻して前のデータを使います。起動・取得失敗確定時も3画面を更新し、GETでは再描画しません。3画面は同じsnapshotと時刻を使い、サーバは端末の表示中viewを保持しません。
