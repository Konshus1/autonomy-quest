# agentricity.com → Azure ACA cutover runbook

Move `agentricity.com` from its current Namecheap hosting to the isolated Azure Container App
`agentricity-site` (`agentricity-rg`), with an Azure **managed TLS certificate** (free, auto-renew).

**Why care about sequencing:** the mobile apps register `https://agentricity.com/privacy.html` and
`/terms.html` with Apple/Google, and Namecheap serves those today. The Azure site serves the SAME paths
(carried over unchanged), so the URLs survive — but we sequence the DNS so ownership is validated
*before* traffic moves, keeping the HTTPS gap to the few minutes of cert issuance.

## Fixed values (from the ACA app, 2026-08-16)

| Thing | Value |
|-------|-------|
| Apex A-record target (env inbound IP) | `48.206.254.32` |
| CNAME target (for `www`) | `agentricity-site.orangebay-a6553ce1.eastus.azurecontainerapps.io` |
| Domain-verification id (`asuid` TXT value) | `4BF46E4C8A6A7F6E25D21C758B61DCE57CCBF3D90CB42DB209E4AD835822FC05` |

## Step 1 — Kevin: add the ownership TXT records at Namecheap (NO traffic change yet)

These prove domain ownership to Azure and do **not** affect what currently serves the site.

| Type | Host | Value | TTL |
|------|------|-------|-----|
| TXT | `asuid` | `4BF46E4C8A6A7F6E25D21C758B61DCE57CCBF3D90CB42DB209E4AD835822FC05` | Automatic |
| TXT | `asuid.www` | `4BF46E4C8A6A7F6E25D21C758B61DCE57CCBF3D90CB42DB209E4AD835822FC05` | Automatic |

(`asuid.www` only if we want the `www` hostname too — recommended, for a www→apex redirect.)

## Step 2 — Claude: add the hostnames (ownership validated via the TXT above, still no cutover)

```bash
az containerapp hostname add -n agentricity-site -g agentricity-rg --hostname agentricity.com
az containerapp hostname add -n agentricity-site -g agentricity-rg --hostname www.agentricity.com
```

## Step 3 — Kevin: the actual cutover at Namecheap (remove the old hosting records first)

Remove Namecheap's current apex hosting/parking records (the existing `@` A record and any URL-redirect
/ CNAME that points the apex at Namecheap hosting), then add:

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | `@` | `48.206.254.32` | Automatic |
| CNAME | `www` | `agentricity-site.orangebay-a6553ce1.eastus.azurecontainerapps.io` | Automatic |

> Namecheap-specific: set the domain's **Nameservers = Namecheap BasicDNS** (Advanced DNS is where
> these records live). If "URL Redirect Record" / "Parking" rows exist on `@` or `www`, delete them —
> they conflict with the A/CNAME.

## Step 4 — Claude: bind the managed certificate

After the A/CNAME records above resolve (check `dig +short agentricity.com` → `48.206.254.32`):

```bash
az containerapp hostname bind -n agentricity-site -g agentricity-rg \
  --hostname agentricity.com --environment agentricity-aca-env --validation-method HTTP
az containerapp hostname bind -n agentricity-site -g agentricity-rg \
  --hostname www.agentricity.com --environment agentricity-aca-env --validation-method CNAME
```

Azure provisions + binds a free managed cert per hostname (a few minutes). Auto-renews.

## Step 5 — Verify, then retire Namecheap hosting

```bash
curl -sSI https://agentricity.com/            | head -1     # 200, valid TLS
curl -sSI https://agentricity.com/privacy.html | head -1     # legal URL intact
curl -sSI https://agentricity.com/terms.html   | head -1
curl -sSI https://www.agentricity.com/         | head -1
```

Only once all four are green on Azure: **Kevin retires the Namecheap hosting/website** (keep the domain
registration + DNS at Namecheap; only the *hosting* product is retired). Email delivery (support@,
kevin.thomas@) is unaffected as long as the MX records are left untouched — do NOT change MX.

## Open items for Kevin
- Confirm we want `www` (recommended) or apex-only.
- What is currently serving the Namecheap site (static hosting? URL redirect?) — so Step 3's "records
  to remove" is exact. Screenshot of the current Advanced DNS page is enough.
- Do NOT touch MX / email records during any of this.
