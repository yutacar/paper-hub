# 開発・変更の提出

まず[README](README.md)、[現行仕様](SPEC.md)、[作業ルール](AGENTS.md)を確認してください。

## ローカル確認

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_public.py --history
```

Python 3.11以上を使います。ファームウェアのホストテストにはC++11対応コンパイラも必要です。テストは依存の導入後、ネットワークなしで動かしてください。

- データ形式は `tests/fixtures/` に従い、仕様外の判断は[設計メモ](docs/design.md)に記録します。
- 計算・取得・レイアウトの変更には、境界値や回帰を確認するテストを追加します。
- ファームウェア変更は[検証済みボード・ライブラリ](docs/firmware.md)でビルドします。
- fixtureの検算値を変える場合、理由と期待値を仕様・テストで合わせます。
- `.env`・`secrets.h`の実値、ログ、DB、生成物をコミットしません。

## IssueとPull Request

不具合はfixtureで再現する手順、期待した表示、実際の表示、検証環境の一般情報を添えます。実データのURL・SSID・メール・個人パスは添えず、ログを匿名化してください。

Pull Requestには具体的な変更と確認結果を記載します。テスト・公開前チェックが通った区切りでコミットし、本リポジトリの公開前チェックはnoreply以外の作者メールを要確認とするため、GitHubのnoreplyアドレスをGitの作者情報に使ってください。過去コミットの作者情報は後からGit設定を変えても変わりません。

GitHub上のCIはPythonテストと履歴を含む公開前チェックを行います。CIに機器接続先やWi-Fiの秘密設定は不要です。
