# paper-hub 作業ルール

- 仕様は SPEC.md が正。仕様にない判断をしたら README からリンクした docs/design.md の「設計メモ」に書く
- このリポジトリは公開する。IP・ホスト名・ユーザー名・絶対パス・Wi-Fi情報をコードやドキュメントに書かない。環境依存の値はすべて .env から読む
- .env は読まない・作らない・コミットしない。必要な項目は .env.sample に追記する
- 開発は macOS、実行は Raspberry Pi 5（arm64）。両方で動くコードにする
- Python は .venv の venv を使う
- データのフィールド名は tests/fixtures/ の JSON に厳密に従う。推測しない
- テストはネットワークなしで通ること
- 区切りごとに pytest を実行し、通ったら git commit する
- 公開前チェック: git grep で 127.0.0.1 と 0.0.0.0 以外の IP が出ないこと
