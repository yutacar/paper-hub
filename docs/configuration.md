# 設定

[READMEに戻る](../README.md)

```sh
cp .env.sample .env
```

利用環境の値は `.env` に記入してください。`.env.sample` は公開用のため全項目が空欄です。`.env`、実行環境、データ、生成物、ファームウェアの秘密情報は Git 管理対象外です。

- `STRATA_MONITOR_URL`、`XANGI_USAGE_URL`: 取得元。
- `STRATA_API_KEY` は任意です。Strata で認証を有効にしている場合は、paper-hub を実行する側の `.env` に同じキーを設定します。キーは Strata の取得時だけ Bearer ヘッダーで送信します。
- `HOST`、`PORT`、`TZ`、`DATA_DIR`: サーバと保存先。`TZ` は IANA タイムゾーン名です。
- `FONT_PATH`、`FONT_PATH_BOLD`: フォント。通常フォントは必須で、太字の省略時は通常フォントを使います。読めないフォントは起動時にエラーになります。
- `USAGE_ORDER`: プロバイダIDのカンマ区切り。
- `USAGE_HIDE`: `provider:group` のカンマ区切り。
- `DEPLOY_TARGET`、`DEPLOY_DIR`: 開発環境のデプロイスクリプト用。接続先と専用の転送ディレクトリを指定します。

起動時に `.env` を読み、同名の環境変数があれば優先します。空欄の任意項目には仕様の既定値を使います。計算層自体は設定ファイルを読みません。
