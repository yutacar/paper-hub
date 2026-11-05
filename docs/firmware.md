# PaperDIYファームウェア

[READMEに戻る](../README.md)

## PaperDIYファームウェア（MacからUSB書き込み）

`firmware/paperdiy/paperdiy.ino` はHTTPClientでPNGをPSRAMへ受信してから `M5.Display.drawPng()` で描画します。M5Stackボード3.3.10、M5Unified 0.2.26、M5GFX 0.2.33の組み合わせでビルドを確認しています。PaperDIY用のFQBNは `m5stack:esp32:m5stack_paperdiy` です。

### Arduino CLI・ボード・ライブラリ

MacにHomebrewとUSBデータケーブルを用意し、リポジトリのルートで実行します。

```sh
brew install arduino-cli
arduino-cli config add board_manager.additional_urls https://static-cdn.m5stack.com/resource/arduino/package_m5stack_index.json
arduino-cli core update-index
arduino-cli core install m5stack:esp32@3.3.10
arduino-cli lib install "M5Unified@0.2.26"
arduino-cli lib install "M5GFX@0.2.33"
arduino-cli board details --fqbn m5stack:esp32:m5stack_paperdiy
```

ボード設定はM5PaperDIYを使い、OPI PSRAMを有効にします。上記ボード定義の既定選択はOPIですが、変更した環境ではboard detailsで確認してください。ライブラリの自動依存で別バージョンのM5GFXが入った場合も、最後のコマンドで検証済みバージョンに揃えます。

### 秘密設定

```sh
cp firmware/paperdiy/secrets.h.sample firmware/paperdiy/secrets.h
```

コピーした `secrets.h` を手元で編集します。サンプルは全項目空欄です。

| 項目 | 設定内容 |
|---|---|
| `WIFI_SSID` | 接続するネットワーク名 |
| `WIFI_PASS` | Wi-Fiパスワード。オープンネットワークでは空欄可 |
| `HUB_URL` | 接続先サーバの `/paper.png` のHTTP URL |
| `NTP_SERVER` | 任意。空欄なら公開NTPサービス `pool.ntp.org`。日本向け例は `ntp.nict.jp` |
| `TZ_RULE` | 任意。空欄なら `UTC0`。日本向け例は `JST-9`。Python側のIANA名とは形式が異なる |

`WIFI_SSID` または `HUB_URL` が空欄なら取得せず、シリアルに設定不足を出します。`secrets.h` は `.gitignore` の `firmware/**/secrets.h` で除外し、サンプルだけを管理します。Wi-Fi情報や取得先URLはシリアルへ出力しません。ファームウェアではPython用の `.env` を読みません。

3画面対応は[サーバを先にデプロイ](deployment.md)し、各viewのX-Paper-Viewを確認してから書き込んでください。HUB_URLは従来の `/paper.png` のままで、ファームが現在のview・電池・RSSIを付加します。

### ビルド・書き込み・シリアル

```sh
arduino-cli compile --fqbn m5stack:esp32:m5stack_paperdiy \
  --build-path out/paperdiy firmware/paperdiy
arduino-cli board list
```

PaperDIYをUSB接続し、上の一覧からポートを確認します。必要なら本体下部の電源ボタンを約2秒押し、赤LEDが点滅したら放してダウンロードモードにします。これは画面更新用のBtnAとは別の操作です。

次の `read` で確認したポートを入力してEnterを押します。ポート名はリポジトリに保存しません。

```sh
read -r PAPERDIY_PORT
arduino-cli upload --fqbn m5stack:esp32:m5stack_paperdiy \
  --port "$PAPERDIY_PORT" --input-dir out/paperdiy firmware/paperdiy
arduino-cli monitor --port "$PAPERDIY_PORT" --config baudrate=115200
```

書き込み後は本体を再起動します。シリアルは115200bpsで、次を出力します。

- `[boot]`: reason=timer/button_a/button_b/button_ab/power_on_or_reset、ESP32の起動理由番号、PSRAM容量、設定の有無。
- `[wifi]`: 成否、接続にかかったミリ秒、RSSI。接続タイムアウトは10秒。
- `[clock]`: NTP・RTC・時刻不明の区別と同期待ち時間。NTP待ちは最大5秒。
- `[http]`: HTTPステータス、Content-Length、受信バイト数、ストリーム処理結果。
- `[draw]` / `[fetch]`: PNG検証・デコード・描画の成否。失敗時の部分描画時刻。
- `[view]`: fromとtoのview名、result=ok（描画成功・状態確定）またはkept（失敗・旧状態維持）。
- `[sleep]`: 次回までの秒数、button_a_wakeup / button_b_wakeupそれぞれの有効状態、時計の有効状態。

通常は取得・描画の直後にUSBシリアルもdeep sleepで切断されます。ログを確認する際はモニターを開いた状態で本体を再起動してください。ホスト側の再接続が必要になる場合があります。

### 動作と実機確認

NTPで時刻を合わせ、ESP32のRTCタイマーでdeep sleep中も保持します。現地時刻7:00〜24:00は600秒、0:00〜7:00は1800秒で復帰し、BtnAでも直ちに起動して現在の画面を取得します。NTP失敗時は保持済みRTC時刻を使い、時刻が一度も得られていない場合は1800秒です。電源断後はNTPの再取得が必要です。

ボタンA（GPIO4）とB（GPIO3）はactive Low入力です。ext1 ANY_LOWで両方を復帰源にし、RTC_PERIPH電源と内部プルアップを明示してdeep sleep中も保持します。復帰後はマスクを読み、両RTCピンをdeinitして通常入力へ戻します。シリアル待ちやM5.beginより前にGPIOを読み、ボタン復帰でもう一方がLowなら同時押しと判別します。タイマー・電源投入ではGPIOレベルで画面を切り替えません。GPIO3はストラッピングピンのため、電源投入・リセット時にBを押し続けず、動作中とdeep sleepからの復帰に使ってください。出力としては駆動しません。

| 起動操作 | 要求する画面 |
|---|---|
| A / タイマー | 現在の画面を更新 |
| B | dashboard → strata → usage → dashboard |
| A+B同時復帰 | dashboard |
| 電源投入 / リセット | dashboardから開始 |

表示中のviewはRTCメモリに保持します。画面の切替は描画成功時だけ確定し、通信・画像検証・X-Paper-View照合の失敗では前の状態と画像を保持します。旧サーバや別viewのPNGはヘッダで拒否します。スリープ前は両ボタンが離れるまで最大2秒待ち、まだLowのボタンだけをその回の復帰マスクから外します。両方がLowならタイマー復帰のみです。Bの操作からWi-Fi接続・取得・全面更新まで数秒かかり、読み込み中の部分表示は行いません。

PNGは最大1MiBで、HTTPClientの `writeToStream()` によりchunked転送もPSRAMへ書き込みます。受信サイズ、全PNGチャンクのCRC、960×540・8bitグレースケールを検査し、PSRAM上の仮描画でデコードを確認してから全画面更新します。受信失敗・サイズ超過・PNG不正・メモリ不足時は前の画像を残し、右下128×28pxだけに×と時刻を表示します。時刻不明なら `--:--` です。最後にWi-Fiを切ってdeep sleepへ移行します。

従来のダッシュボードはUSB書き込み・実機表示・電池残量表示を確認済みです。3画面対応はホストテストとM5PaperDIY向けビルドを確認済みで、以下の実機動作と電池持続時間の長期計測は未確認です。

- [ ] Bでダッシュボード→Strata詳細→AI利用量詳細→ダッシュボードと巡回する。
- [ ] 各画面でAを押すと、同じ画面のまま更新される。
- [ ] 詳細画面のままタイマー復帰して、同じ画面が更新される。
- [ ] A+Bの同時復帰でダッシュボードになる（A→B、B→Aの少しずれた押下も確認）。
- [ ] サーバ停止中にBを押すと、前の画面に×が出る。サーバ復帰後の次のBで画面を飛ばさない。
- [ ] Aだけ、Bだけ、両方を押し続けても復帰ループにならず、ログの有効マスクと一致する。
- [ ] 電源を入れ直すとダッシュボードから始まる。
- [ ] USBシリアル115200bpsで各復帰理由とviewのfrom/to/resultを確認する。

参照: [M5Stack公式PaperDIY書き込みガイド](https://docs.m5stack.com/en/arduino/paperdiy/program)、[公式表示API](https://docs.m5stack.com/en/arduino/paperdiy/display)、[ESP32-S3 RTC時刻仕様](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/system_time.html)。
