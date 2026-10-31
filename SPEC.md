# paper-hub 仕様書（M5Stack PaperDIY ダッシュボード）

## 目的

ローカル推論エンジン Strata のモニター情報と、xangi の AI 利用量を、M5Stack PaperDIY（4.7インチ E-Ink、960×540、16階調グレー）に表示する。

構成：

```
[推論マシン]                          [Raspberry Pi 5（SSD 起動）]
 Strata /metrics ──(LAN/Tailscale)──►  paper-hub ◄── xangi /api/usage（同一ホスト）
                                          │
                                          │ LAN（GET /paper.png）
                                          ▼
                                     [PaperDIY]（取得→描画→deep sleep）
```

- 開発は macOS（MacBook Air）で行い、Raspberry Pi 5 へ転送して実行する。
- 描画はすべて paper-hub 側（Python）で行う。PaperDIY は PNG を表示するだけ。
- paper-hub が公開するのは読み取り専用の画像・JSON だけ。

---

## 0. 公開リポジトリとしての前提

このコードは GitHub で公開する。以下を守ること。

- **実環境のIPアドレス、ホスト名、ユーザー名、ホームディレクトリのパス、Wi-Fi情報をコードやドキュメントに書かない。** 公開NTPサービスの既定値・例と、値を伏せた設定プレースホルダーは記載可。
- 環境ごとの値はすべて `.env` から読む。リポジトリには値の入っていない `.env.sample` だけを含める。
  利用者は `cp .env.sample .env` して編集する。
- ファームウェアの秘密情報は `firmware/paperdiy/secrets.h`（Git 管理外）に置き、`secrets.h.sample` を同梱する。
- `.gitignore` に `.env`、`.venv/`、`data/`、`out/`、`firmware/**/secrets.h`、`__pycache__/` を含める。
- テスト用の JSON は `tests/fixtures/` に置く。値は実データ由来でよいが、ホスト名・パス・IP を含まないことを確認する。
- README は日本語で、セットアップから常駐化までを第三者が再現できる内容にする。ライセンスは MIT。

---

## 1. paper-hub（サーバ）

### 環境

- 実行: Raspberry Pi 5（arm64、Raspberry Pi OS 64bit、SSD 起動）
- 開発・テスト: macOS（Apple Silicon）。どちらでも同じコードで動くこと
- Python 3.11 以上 + venv（`.venv`）。FastAPI + uvicorn + Pillow + httpx + python-dotenv、テストは pytest
- Pi では `systemd --user` サービスとして常駐（`loginctl enable-linger` 前提）
- 履歴は SQLite（`DATA_DIR/history.db`、WAL モード）。SSD 起動なので書き込み量の制約はない
- フォントは `FONT_PATH` で指定（Noto Sans CJK JP 推奨）。macOS と Linux でパスが違うため、コードに固定パスを書かない

### 設定（`.env`）

`.env.sample` の全項目は空欄にする。利用環境の値はローカルで設定する:

```dotenv
# データ取得元（必須）
STRATA_MONITOR_URL=
XANGI_USAGE_URL=

# サーバ
HOST=
PORT=
TZ=
DATA_DIR=

# 描画（FONT_PATH は必須、BOLD は省略時に通常フォントを使用）
FONT_PATH=
FONT_PATH_BOLD=

# AI利用量の表示
USAGE_ORDER=
USAGE_HIDE=

# デプロイ（開発環境のみ）
DEPLOY_TARGET=
DEPLOY_DIR=
```

- 設定サンプルの各項目には、値を伏せた書き方の例と既定値をコメントで示す。代入行は空欄にする。
- 必須値（`STRATA_MONITOR_URL`、`XANGI_USAGE_URL`、`FONT_PATH`）が未設定なら、起動時に分かりやすいエラーで終了する。
- `FONT_PATH_BOLD` が未設定なら `FONT_PATH` を使う。

### ポーリング

| 対象 | 間隔 | タイムアウト |
|---|---|---|
| Strata | 30秒 | 3秒 |
| xangi | 60秒 | 3秒 |

- 各試行は通信全体3秒。失敗したら2秒後に1回だけ再試行し、各失敗の例外クラス・メッセージ・HTTPステータス・レスポンス先頭200文字をログに記録する。
- 最終的な失敗時も前回値を保持する。最終成功から300秒以上の場合だけ、見出し右側の更新時刻の隣に「取得失敗 HH:MM」を表示し、本文の位置は変えない。未成功なら「データなし」。
- Strata への最終取得成功から10分以上経ったら、Strata エリアを「Strata オフライン（最終 HH:MM）」表示に切り替える。
- 一度も取得できていない場合はエリア全体を「データなし」とする。

### 履歴（SQLite）

- Strata 取得ごとに `hardware.gpu_util`、`hardware.gpu_power`、`hardware.cpu`、`hardware.disk_read_mb`、`live.tok_s` を保存。電力の直近60分（最大120点）をスパークラインに使う。live t/s・GPU使用率・CPU・ディスクのグラフは表示しない。
- Strataの最新順requestsから未再利用トークンが512以上の最初の1件を選び、有効なPrefillとリクエスト時刻をSQLiteに保存する。7日削除の対象外として最後の1件を保持する。
- xangi 取得ごとに各 window の `usedPercent` を保存（今回は描画しない。将来の推移グラフ用）。
- 7日より古い行は1時間ごとに削除。起動時は DB から直近60分を読み込んでから描画する。

### エンドポイント

- `GET /paper.png` … 960×540、グレースケール PNG（16階調に量子化、ディザなし）。クエリ `bat`（%）と `rssi`（dBm）を受け取ったら保存し、次回描画のフッターに出す
- `GET /paper.json` … 描画に使った整形済みの値（デバッグ用）
- `GET /healthz` … `{"ok": true, "strata_age_s": .., "xangi_age_s": ..}`

PNG は毎リクエスト生成せず、データ更新時に再生成してメモリにキャッシュする。

### CLI

- `python -m paperhub.render --strata tests/fixtures/strata_monitor.json --usage tests/fixtures/xangi_usage.json --out out/paper.png --now <unix秒>`
  ネットワークなしで PNG を生成する（Mac での見た目確認とテスト用）。

---

## 2. Strata の表示項目

元データ: `STRATA_MONITOR_URL` のレスポンス。`r0 = requests[0]`（最新リクエスト）。フィールド名は `tests/fixtures/strata_monitor.json` に厳密に従う。`null`・欠落は「—」。

| 表示 | 計算 |
|---|---|
| 状態 | `live.state`を先頭大文字にする。`idle` 以外なら `live.phase` と `live.tok_s` も表示 |
| モデル | `engine.model` |
| Decode t/s | `r0.decode_tok_s` |
| Prefill t/s | 最新順requestsのうち `prompt_tokens - reused >= 512` の最初の1件で `(prompt_tokens - reused) / (prompt_ms / 1000)`。計算不能ならSQLiteの最後の有効値を日時付きで使い、保存値もなければ小さい「—」 |
| VRAM | `hardware.gpu_mem_used / 2^30`（小数1桁）/ `hardware.gpu_mem_total / 2^30`（整数）GB |
| experts cached | `engine.expert_slots` |
| GPU | `hardware.gpu_util` %、`hardware.gpu_temp` °C |
| 電力 | `hardware.gpu_power`（整数）/ `hardware.gpu_power_limit`（整数）W |
| PCIe | `Gen{hardware.gpu_pcie_gen} x{hardware.gpu_pcie_width}` |
| CPU | `hardware.cpu` % |
| Context | `(r0.prompt_tokens + r0.output_tokens) / engine.max_context` を % と `13k / 64K` 形式 |
| RAM | `hardware.ram_used / 2^30`（小数1桁）/ `hardware.ram_total / 2^30`（整数）GB |
| 最終リクエスト | `time - r0.time` を「2日18時間前」「35分前」形式 |
| キャッシュ再利用率 | `totals.reused / totals.prompt_tokens` |

`requests` が空の場合は Decode/Context/直近リクエストを「—」にし、Prefillは保存値があれば使用する。

現fixtures: Prefillは該当なし、Context ≈ 0.377%（247 / 64K）、VRAM 14.6 / 16 GB、RAM 61.7 / 79 GB、キャッシュ再利用率 ≈ 73.846%。初期仕様のPrefill ≈ 131・Context ≈ 19%などは別の合成入力で検算する。

### 直近リクエスト（最大3件）

- 画面はHH:MM。JSONの整形値には当日以外の日付も残す（`time` はUnix秒）。
- 時刻 / 状態 / prompt→output / t/s / 秒を固定列に配置し、数値列は右揃え。22px、行高33px以上。
- 状態: `finish` が `length` → `MaxTok`、`stop` → `Done`、その他はそのまま
- `prompt_tokens → output_tokens`（prompt は `12.6k` のように k 表記）
- decode t/s、`duration_s`

---

## 3. xangi AI 利用量の表示項目

元データ: `XANGI_USAGE_URL` のレスポンス。構造は `providers[] → groups[] → windows[]`。フィールド名は `tests/fixtures/xangi_usage.json` に厳密に従う。

### 計算

- 使用率: `usedPercent` を四捨五入して整数%
- **目安（ペース）%**（JSON には無いので計算）:
  `remaining = resetsAt - now`
  `pace = clamp((windowDurationMins*60 - remaining) / (windowDurationMins*60) * 100, 0, 100)`
  検算: Claude Code 週次、`resetsAt=1793509200`、`now=1793410719` → 約84%
- リセット表示: `resetsAt <= now` は「リセット済」で目安線・反転を描かない。残り0秒超60秒未満は「まもなく」。それ以外は `windowDurationMins <= 300` →「あと4h41m」、長い枠 →「11/1 14:00」（TZ）。
- `resetsAt` は Unix 秒（小数あり）
- `sourceType` が `live` 以外、または `sourceUpdatedAt` が10分以上前なら、プロバイダ名の横に「(古)」

### 並びと省略

- プロバイダは `USAGE_ORDER` の順。リストにないものは末尾。
- `USAGE_HIDE`（`provider:group` のカンマ区切り）に該当する group は表示しない。
- groupのすべてのwindowが0%なら個別表示せず、プロバイダ別に「0%: Antigravity（Gemini, Argon, Claude・GPT）」の形式で1行に集約する。既知groupを短縮名にし、その他はlabelから「共通枠」「専用枠」を除き、幅超過時は末尾を「…」で省略する。
- group見出しは `provider.label` + `group.label`（同名なら重複を省く。`planType` があれば併記）。

### バーの描画

- ラベル（5h/週）・横バー（幅146px、高さ14px、枠線2px）・使用率・リセットを1行にし、バーに使用率を塗りつぶす。
- 目安の位置に太さ3pxの縦線を、バーより上下に3pxはみ出して描く。
- **使用率 > 目安** の行は反転表示（黒地に白文字）。

---

## 4. レイアウト（960×540）

- 左右比率は52:48程度。区切り線は2px。
- 主要数値Decode/Prefillは48px。値なしのPrefillは24pxの「—」。本文は最小22px。
- 数値と単位を同じベースラインに配置する。温度は48°C、電力上限は整数、expertsは「experts 3189」。
- 左はモデル、主要数値、VRAM・RAM・Context百分率・GPU温度・電力。低優先度のPCIe・CPUは画面に表示しない。
- 直近最大10リクエストのdecode t/sを幅300×高さ40pxの棒グラフにし、最大値を右上に表示する。古い順に配置し、nullは空欄、10件未満は右側から使う。
- 電力スパークラインは幅140×高さ32px、線幅2px。ラベルの下にグラフ、右上に現在値。
- 直近リクエストは最大3件、固定列、1件1行、22px・行高33px。
- 右はgroup見出しと1行window。0%集約も末尾1行。溢れたgroupは件数、文字列は省略記号で示す。
- フッターは更新時刻・最終リクエスト・再利用率。電池・RSSIは受信した値だけを「電池 87%  -58dBm」の形式で表示する。

---

## 5. デプロイ（Mac → Pi）

`scripts/deploy.sh`（開発マシンで実行）:

1. `.env` から `DEPLOY_TARGET` と `DEPLOY_DIR` を読む（未設定ならエラー）
2. `pytest` を実行し、失敗したら中止
3. `rsync -av --delete` で転送。除外: `.git/`、`.venv/`、`.env`、`data/`、`out/`、`__pycache__/`、`firmware/`
4. Pi 側で `python3 -m venv .venv`（無ければ）→ `pip install -r requirements.txt`
5. `systemctl --user restart paper-hub`（ユニットが有効な場合のみ）→ `/healthz` を確認して結果を表示

Pi 側の `.env` は転送しない（Pi 上で `.env.sample` から別途作る）。

`deploy/paper-hub.service`（systemd ユーザーユニット）を同梱する。`WorkingDirectory=%h/paper-hub`、`EnvironmentFile=%h/paper-hub/.env` とし、ユーザー名や絶対パスを書かない。

---

## 6. PaperDIY ファームウェア（Arduino）

- 配置: `firmware/paperdiy/`
- ボード: M5PaperDIY（M5Stack board package 3.3.10 以上、M5Unified 0.2.26 以上、M5GFX 0.2.33 以上）
- `secrets.h`（Git管理外）に `WIFI_SSID`、`WIFI_PASS`、`HUB_URL`、`NTP_SERVER`、`TZ_RULE`。代入行が空欄の `secrets.h.sample` を同梱。`NTP_SERVER` / `TZ_RULE` は任意で、空欄なら `pool.ntp.org` / `UTC0`。日本向けの `ntp.nict.jp` / `JST-9` はコメント例にする
- 動作:
  1. 起動（タイマー or ボタン）
  2. Wi-Fi 接続（10秒でタイムアウト）
  3. HTTPClientで `GET {HUB_URL}?bat=<電池%>&rssi=<dBm>` をPSRAMへ全量受信。最大1MiB。ステータスと受信サイズをログ出力
  4. サイズ・PNG構造・CRC・PSRAM上のデコードを検証後、`M5.Display.drawPng()` で全面描画（16階調）
  5. Wi-Fi を切って deep sleep
- 更新間隔: 7:00〜24:00 は10分、0:00〜7:00 は30分（NTP で時刻取得、RTC に保持）
- ボタンA: GPIO4のLowでdeep sleepから復帰して更新。押しっぱなし時は最大2秒待ち、その回はタイマー復帰のみ。
- 失敗時: 前回の画面を残し、右下に小さな「×」と時刻だけ部分描画して sleep
- 各段階を115200bpsでログ出力。SSID・パスワード・取得先URLは出力しない。
- RTCはESP32の内部タイマーを使い、未同期時は1800秒を選ぶ。
- 書き込みはMacにUSB接続して行う。READMEからリンクするファームウェア手順にarduino-cliの導入・ビルド・書き込みを記載

---

## 7. 完了条件

- `tests/fixtures/` だけで（ネットワークなしで）`out/paper.png` を生成でき、pytest が通る
- 単体テスト: Prefill、Context、ペース%（検算値）、リセット表示、相対時間、Strata オフライン判定、null 処理
- 片方の取得が失敗しても、もう片方は正常に描画される
- Mac で `uvicorn` を起動し、`.env` の実データ URL から `/paper.png` を表示できる
- `scripts/deploy.sh` で Pi に転送し、`systemctl --user enable --now paper-hub` で常駐、Pi 再起動後も起動する
- `git grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}'` で、`127.0.0.1` と `0.0.0.0` 以外の IP が出てこない
- README（日本語）から、Mac開発・Pi導入・設定・デプロイ・ファーム書き込み・ufwの8090番開放の手順を辿れること
- GitHubのCIでテストと公開前チェックを行う。実機の完了条件は実機で別途確認する
