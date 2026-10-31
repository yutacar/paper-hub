# Raspberry Piへの導入とデプロイ

[READMEに戻る](../README.md)

## Raspberry Piのセットアップ・常駐化

Raspberry Pi OS 64bitでPython 3.11以上、venv、pip、日本語フォントを用意します。リポジトリをホーム直下の `paper-hub` に配置してください。ユニットの `%h` が利用者のホームを解決します。

```sh
cd "$HOME/paper-hub"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.sample .env
```

Pi専用の `.env` を編集し、まず前述の起動コマンドとhealthzを確認します。Macのフォントパスや取得先をそのまま流用しないでください。その後フォアグラウンドのサーバを停止して、ユーザーユニットを導入します。

```sh
mkdir -p "$HOME/.config/systemd/user"
cp deploy/paper-hub.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now paper-hub.service
sudo loginctl enable-linger "$USER"
systemctl --user status paper-hub.service
journalctl --user -u paper-hub.service -n 50
```

lingerによりログイン前・ログアウト後も起動できます。Piを再起動した後もstatusとhealthzを確認してください。別ディレクトリに配置する場合はユニットの `WorkingDirectory`、`EnvironmentFile`、`ExecStart` を利用環境に合わせます。

ufwを使用する場合、既定ポートは次で許可できます。設定したポートが異なる場合は番号を合わせます。

```sh
sudo ufw allow 8090/tcp
```

## Macからデプロイ

開発環境にSSHとrsyncを用意し、SSHでPiへ接続できる状態にします。開発側の `.env` だけに `DEPLOY_TARGET` と `DEPLOY_DIR` を設定します。Pi側のユニットと `.env` は上の手順で先に用意してください。

```sh
scripts/deploy.sh
```

ローカルpytestが成功した場合のみ転送先の専用ディレクトリを準備し、`rsync -av --delete` で同期します。転送先のリポジトリ内で削除したファイルはPi側でも削除されるため、アプリ専用ディレクトリを使ってください。`.git`、`.venv`、`.env`と追加の環境ファイル、`data`、`out`、Pythonキャッシュ、`firmware`を除外し、空の `.env.sample` は転送します。Pi側の除外ファイルは削除しません。

Pi側ではvenvを作成・依存をインストールし、有効なユーザーサービスだけを再起動します。最大10回healthzを確認して結果を表示します。未有効でサーバも動いていなければヘルスチェックは失敗します。SSH・rsync・テスト・依存導入・ヘルスチェックの失敗時は処理を中断します。ユニットを変更したときはPi側でも再コピーとdaemon-reloadが必要です。
