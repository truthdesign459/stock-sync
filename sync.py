#!/usr/bin/env python3
"""Daily sold-out sync: supplier sites -> Shopify.

Every in-stock Shopify product whose `custom.supplier` metafield points at a site listed
in sites.json is checked. If the supplier page says the item is sold out (or the page is
gone), the Shopify stock is set to 0.

Auth: with SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET set (GitHub Actions), the script gets
a token with the client credentials grant and calls the Admin API directly. Without them it
falls back to the local Shopify CLI (`shopify store execute`), which is handy for testing.

    python3 sync.py            # sync
    python3 sync.py --dry-run  # report only, change nothing
"""
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta, timezone

STORE = os.environ.get("SHOPIFY_STORE", "japan-baseball-cards.myshopify.com")
LOCATION = os.environ.get("SHOPIFY_LOCATION", "gid://shopify/Location/80811294902")
API_VERSION = "2026-10"
HERE = os.path.dirname(os.path.abspath(__file__))
DRY_RUN = "--dry-run" in sys.argv
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; RisingSunStockSync/1.0)"

SUMMARY = []

def log(msg):
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)

# ---------- Shopify ----------

_token = None

def token():
    global _token
    if _token is None:
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": os.environ["SHOPIFY_CLIENT_ID"],
            "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"],
        }).encode()
        req = urllib.request.Request(f"https://{STORE}/admin/oauth/access_token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        _token = json.load(urllib.request.urlopen(req, timeout=30))["access_token"]
    return _token

def gql(query, variables=None, mutation=False):
    if os.environ.get("SHOPIFY_CLIENT_ID"):
        req = urllib.request.Request(
            f"https://{STORE}/admin/api/{API_VERSION}/graphql.json",
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token()})
        res = json.load(urllib.request.urlopen(req, timeout=60))
        if res.get("errors"):
            raise RuntimeError(res["errors"])
        return res["data"]
    cmd = ["shopify", "store", "execute", "--store", STORE, "--json", "--no-input", "--query", query]
    if variables is not None:
        cmd += ["--variables", json.dumps(variables)]
    if mutation:
        cmd.append("--allow-mutations")
    env = dict(os.environ, SHOPIFY_CLI_AGENT_INFO="n:stock-sync|v:1|p:none|m:none",
               PATH=os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin")
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    # --json prints one progress object per line, then the (pretty-printed) result
    text = "\n".join(l for l in out.stdout.splitlines()
                     if not l.startswith('{"type":"progress"') and not l.startswith("<claude-code-hint"))
    if out.returncode != 0 or not text.strip():
        raise RuntimeError(f"shopify execute failed: {text.strip()[-800:] or out.stderr.strip()[-800:]}")
    return json.loads(text)

def products():
    """In-stock products that have a supplier URL, with their stock at LOCATION."""
    q = """query($after: String, $loc: ID!) {
      products(first: 100, after: $after, query: "inventory_total:>0") {
        pageInfo { hasNextPage endCursor }
        nodes { id title status
          metafield(namespace: "custom", key: "supplier") { value }
          variants(first: 20) { nodes { inventoryItem { id
            inventoryLevel(locationId: $loc) { quantities(names: ["available"]) { quantity } } } } } } } }"""
    after = None
    while True:
        d = gql(q, {"after": after, "loc": LOCATION})["products"]
        for p in d["nodes"]:
            if not p["metafield"]:
                continue
            urls = json.loads(p["metafield"]["value"])
            items = []
            for v in p["variants"]["nodes"]:
                lvl = v["inventoryItem"]["inventoryLevel"]
                qty = lvl["quantities"][0]["quantity"] if lvl else 0
                if qty > 0:
                    items.append((v["inventoryItem"]["id"], qty))
            if urls and items:
                yield p, urls[0], items
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]

def set_quantity(item_id, current, quantity=0):
    m = """mutation($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) @idempotent(key: "%s") { userErrors { field message } } }""" % uuid.uuid4()
    inp = {"name": "available", "reason": "correction",
           "quantities": [{"inventoryItemId": item_id, "locationId": LOCATION,
                           "quantity": quantity, "changeFromQuantity": current}]}
    errs = gql(m, {"input": inp}, mutation=True)["inventorySetQuantities"]["userErrors"]
    if errs:
        raise RuntimeError(errs)

# ---------- supplier sites ----------

def load_sites():
    sites = json.load(open(os.path.join(HERE, "sites.json")))["sites"]
    by_domain = {}
    for s in sites:
        for d in s["domains"]:
            by_domain[d.lower()] = s
    return by_domain

def fetch(url, site):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=30).read()
            return raw.decode(site.get("encoding", "utf-8"), "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None          # page removed -> treated as sold out
            if attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(5)

def in_stock(url, site):
    """True/False from the site's rules. A rule set can use either or both lists."""
    html = fetch(url, site)
    if html is None:
        return False
    if any(s in html for s in site.get("sold_out_if_contains", [])):
        return False
    rule = site.get("in_stock_if_contains")
    if rule:
        return any(s in html for s in rule)
    return True

# ---------- main ----------

def main():
    sites = load_sites()
    # group work by site so one broken site can be stopped without touching the others
    work, unknown = {}, 0
    for p, url, items in products():
        site = sites.get(urllib.parse.urlparse(url).hostname or "")
        if not site:
            unknown += 1
            continue
        work.setdefault(site["name"], (site, []))[1].append((p, url, items))

    total_checked = total_zeroed = total_errors = 0
    for name, (site, entries) in work.items():
        sold, errors = [], 0
        for p, url, items in entries:
            try:
                if not in_stock(url, site):
                    sold.append((p, url, items))
            except Exception as e:
                errors += 1
                log(f"ERROR [{name}] {p['title']} | {url} | {e}")
            time.sleep(site.get("delay_seconds", 1))

        # Safety stop: if a large share suddenly looks sold out, the site probably changed
        # its page layout. Change nothing for this site and report it instead.
        limit = site.get("max_sold_out_ratio", 0.5)
        if len(sold) >= 5 and len(sold) > limit * len(entries):
            errors += 1
            log(f"ERROR [{name}] {len(sold)}/{len(entries)} items look sold out; skipped as a safety stop. "
                f"Check the site's page layout and the rules in sites.json.")
            SUMMARY.append(f"- **{name}: safety stop** ({len(sold)}/{len(entries)} looked sold out; nothing changed)")
            sold = []

        for p, url, items in sold:
            try:
                if not DRY_RUN:
                    for item_id, qty in items:
                        set_quantity(item_id, qty)
                log(f"SOLD OUT -> stock 0{' (dry run)' if DRY_RUN else ''}: [{name}] {p['title']} | {url}")
                SUMMARY.append(f"- [{name}] {p['title']} → 在庫0{'（dry run）' if DRY_RUN else ''} — {url}")
            except Exception as e:
                errors += 1
                log(f"ERROR [{name}] {p['title']} | {url} | {e}")

        log(f"[{name}] checked={len(entries)} sold_out={len(sold)} errors={errors}")
        total_checked += len(entries); total_zeroed += len(sold); total_errors += errors

    log(f"done: checked={total_checked} set_to_zero={total_zeroed} errors={total_errors} "
        f"unknown_supplier_sites={unknown}{' (dry run)' if DRY_RUN else ''}")

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(f"## 在庫同期 {datetime.now(JST):%Y-%m-%d %H:%M} JST{'（dry run）' if DRY_RUN else ''}\n\n"
                    f"確認 {total_checked} 件 / 在庫0にした {total_zeroed} 件 / エラー {total_errors} 件\n\n"
                    + ("\n".join(SUMMARY) + "\n" if SUMMARY else ""))
    sys.exit(1 if total_errors else 0)

if __name__ == "__main__":
    main()
