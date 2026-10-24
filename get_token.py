#!/usr/bin/env python3
"""One-time helper: get an offline Admin API access token for the Stock Sync app.

Used when the client credentials grant is not allowed (app and store in different
organizations). Run it on your own Mac:

    python3 get_token.py

It asks for the app's Client ID and Client secret, opens the store's approval page in the
browser, receives the code on http://localhost:3456/callback and exchanges it for a token.
The token is copied to the clipboard (never printed) so you can paste it into the GitHub
secret SHOPIFY_ACCESS_TOKEN.
"""
import getpass, hashlib, hmac, http.server, json, os, secrets, subprocess, urllib.parse, urllib.request, webbrowser

STORE = os.environ.get("SHOPIFY_STORE", "japan-baseball-cards.myshopify.com")
SCOPES = "read_products,read_inventory,write_inventory"
PORT = 3456
REDIRECT = f"http://localhost:{PORT}/callback"

client_id = input("Client ID: ").strip()
client_secret = getpass.getpass("Client secret (入力しても表示されません): ").strip()
state = secrets.token_urlsafe(16)
result = {}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/callback":
            self.send_response(404); self.end_headers(); return
        q = dict(urllib.parse.parse_qsl(u.query))
        sent_hmac = q.pop("hmac", "")
        msg = "&".join(f"{k}={v}" for k, v in sorted(q.items()))
        ok = (q.get("state") == state and q.get("shop") == STORE and
              hmac.compare_digest(hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).hexdigest(), sent_hmac))
        if ok:
            result["code"] = q["code"]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<h2>承認を受け取りました。ターミナルに戻ってください。</h2>" if ok
                          else "<h2>確認に失敗しました。ターミナルを確認してください。</h2>").encode())

url = (f"https://{STORE}/admin/oauth/authorize?" +
       urllib.parse.urlencode({"client_id": client_id, "scope": SCOPES, "redirect_uri": REDIRECT, "state": state}))
server = http.server.HTTPServer(("localhost", PORT), Handler)
print("ブラウザで承認画面を開きます。開かない場合は次のURLを開いてください:\n" + url)
webbrowser.open(url)
while "code" not in result:
    server.handle_request()
    if "code" not in result:
        print("承認の確認に失敗しました（Client secret の入力ミスの可能性）。Ctrl-C で中止してやり直してください。")

req = urllib.request.Request(f"https://{STORE}/admin/oauth/access_token",
                             data=json.dumps({"client_id": client_id, "client_secret": client_secret,
                                              "code": result["code"]}).encode(),
                             headers={"Content-Type": "application/json"})
res = json.load(urllib.request.urlopen(req, timeout=30))
token = res["access_token"]

# check the token works before handing it over
chk = urllib.request.Request(f"https://{STORE}/admin/api/2026-10/graphql.json",
                             data=json.dumps({"query": "{ shop { name } }"}).encode(),
                             headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token})
shop = json.load(urllib.request.urlopen(chk, timeout=30))["data"]["shop"]["name"]

subprocess.run(["pbcopy"], input=token.encode(), check=True)
print(f"\n成功: ストア「{shop}」のトークンを取得し、クリップボードにコピーしました。")
print(f"権限: {res.get('scope')}")
if res.get("expires_in"):
    print(f"注意: このトークンは {res['expires_in']} 秒で期限切れになります。Claude に伝えてください。")
print("GitHub の Secrets に Name = SHOPIFY_ACCESS_TOKEN として貼り付けてください（このあと別のものをコピーしないように）。")
