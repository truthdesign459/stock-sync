# 在庫同期（仕入れ先サイト → Shopify）

毎日 0:00（日本時間）に GitHub Actions で動きます。

**動き**
- 在庫がある商品のうち、メタフィールド `custom.supplier`（仕入れ先）にURLが入っている商品を確認します。
- そのURLのサイトが `sites.json` に登録されていれば、仕入れ先のページを開きます。
- 売り切れ（またはページが削除済み）なら、Shopify の在庫を 0 にします。

## 仕入れ先サイトを追加する

`sites.json` の `sites` に1件追加します。

```json
{
  "name": "サイト名",
  "domains": ["www.example.co.jp"],
  "in_stock_if_contains": ["カートに入れる"],
  "sold_out_if_contains": ["SOLD OUT"]
}
```

| 項目 | 意味 |
|---|---|
| `domains` | 仕入れ先URLのドメイン。商品の `custom.supplier` のURLと一致させます |
| `in_stock_if_contains` | ページにこの文字があれば「在庫あり」。どれもなければ売り切れ扱い |
| `sold_out_if_contains` | ページにこの文字があれば「売り切れ」 |
| `encoding`（任意） | ページの文字コード。既定は `utf-8`。Shift_JIS のサイトは `"shift_jis"` |
| `delay_seconds`（任意） | ページを開く間隔（秒）。既定は 1 |
| `max_sold_out_ratio`（任意） | 1回で半分以上（既定 0.5）が売り切れに見えたら、サイトの作りが変わった可能性があるため何も変更せず止めます |

ルールは片方だけでも使えます。新しいサイトを追加したら、Actions から dry run で確認してください。

## 手動で実行する

GitHub の **Actions → Stock sync → Run workflow**。
`Dry run` にチェックを入れると、確認だけして在庫は変更しません。

結果は実行ごとの画面（Summary）に表示されます。エラーがあると実行が失敗になり、GitHub からメールが届きます。

## 設定（Secrets）

**Settings → Secrets and variables → Actions** に登録します。

- `SHOPIFY_ACCESS_TOKEN`

アプリ（Dev Dashboard の Stock Sync、必要な権限: `read_products`, `read_inventory`, `write_inventory`）とストアが別の組織にあるため、アクセストークンを使います。

トークンは、Mac で `python3 get_token.py` を実行して一度だけ取得します。このとき、アプリの「許可されたリダイレクトURL」に `http://localhost:3456/callback` が登録されている必要があります。

アプリとストアが同じ組織にある場合は、トークンの代わりに `SHOPIFY_CLIENT_ID` と `SHOPIFY_CLIENT_SECRET` の2つを登録しても動きます。
