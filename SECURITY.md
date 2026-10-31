# セキュリティ

paper-hubは信頼できるLANでの運用を想定します。認証機能・TLS終端は同梱していません。サーバとPaperDIY間の通信はHTTPです。

## 設定とデータ

- `.env`と`firmware/paperdiy/secrets.h`はローカル管理し、リポジトリには空欄のサンプルだけを含めます。
- 履歴DBには使用量やリクエスト時刻、最後の有効Prefill、電池・RSSIが保存されます。
- 取得失敗の診断ログは例外メッセージやレスポンス先頭200文字を含み、URLなどの環境情報が入り得ます。
- 画像・paper.jsonも使用量を含むため、共有する場合は表示内容を確認してください。

公開前の点検対象・制限は[公開・プライバシー確認](docs/privacy.md)を参照してください。

## 問題の報告

秘密情報を公開Issueへ貼らないでください。GitHubでPrivate vulnerability reportingが有効な場合はSecurityから非公開報告を利用できます。未有効なら、まず詳細を含めずに非公開の報告経路を管理者へ確認してください。専用メールアドレスは設定していません。

秘密が履歴に入った場合、現在のファイルを削除するだけでは履歴から消えません。該当する認証情報を失効・変更し、履歴の扱いを関係者と確認してください。本リポジトリの点検スクリプトは履歴を自動で書き換えません。

[GitHub公式の非公開報告設定](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)
