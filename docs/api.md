# 計算API

[READMEに戻る](../README.md)

## 計算API

```python
import json
from paperhub import format_strata, format_usage

with open("tests/fixtures/strata_monitor.json", encoding="utf-8") as stream:
    strata = json.load(stream)
with open("tests/fixtures/xangi_usage.json", encoding="utf-8") as stream:
    usage = json.load(stream)

# tz は呼び出し側の設定値を渡す
# strata_view = format_strata(strata, tz=tz)
# usage_view = format_usage(usage, now=now, tz=tz, order=order, hide=hide)
```

`format_strata(payload, *, tz)` は表示文字列と計算値を返します。`prefill_value` は毎秒トークン数、`context_value` は使用トークン数・上限・百分率、`cache_reuse_ratio` は0〜1の比率です。`recent_requests` は元の順序で最大3件です。

`format_usage(payload, *, now, tz, order="", hide="")` は並び替え・非表示処理後の `providers` と、元の0%group名を持つ `zero_groups`、プロバイダごとの短縮名を持つ `zero_providers`、末尾に表示する `zero_summary` を返します。各windowの `used_percent` は丸めた整数、`pace_percent` は丸めていない百分率です。`inverted` は使用率がペースを超えているかを表します。バーの描画側はこの値を使えます。

個別の計算も利用できます。

- `paperhub.strata`: `prefill_tokens_per_second`、`context_usage`、`relative_time`、`is_strata_offline`
- `paperhub.usage`: `pace_percent`、`reset_label`、`provider_is_stale`

詳細画面でも `recent_requests` は最大3件です。`render_png(snapshot, settings, mode="dashboard")` はdashboard/strata/usageを受け取り、CLIでは `--view` で選びます。画面切替のためにformat_strata/format_usageの計算APIは変更していません。
