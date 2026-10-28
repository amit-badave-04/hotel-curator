# Moving behind Cloudflare

The checklist for putting hotel-curator behind Cloudflare on its own
domain. Until then the site runs on `https://hotel-curator.fly.dev`
([fly.md](fly.md)). The design is in
[ADR 011](../decisions/011-deployment-fly-cloudflare.md) ("Two edge
modes"). `<domain>` below is the apex, for example `example.org`.

Cloudflare's dashboard labels and plan limits were not checked from the
build environment: follow the meaning of each step if a label has moved.

## What changes

| | Before (Fly mode) | After (Cloudflare mode) |
|---|---|---|
| Public address | `https://hotel-curator.fly.dev` | `https://<domain>`; `www.<domain>` redirects to it |
| Browser TLS | Fly's certificate | Cloudflare's edge certificate |
| Cloudflare to origin | — | TLS through Fly's TCP passthrough to nginx, which presents the Cloudflare Origin Certificate and requires Cloudflare's origin-pull client certificate |
| Client address for rate limits | `Fly-Client-IP` | `CF-Connecting-IP`, believed only from Cloudflare's ranges |
| `hotel-curator.fly.dev` | the site | refused: no handshake for that name, 400 without Cloudflare's certificate |
| Fly config | `web/fly.toml` | `web/fly.cloudflare.toml` |

## Before you start

- The domain's zone is active in Cloudflare (its nameservers point there).
  The Free plan covers every step below.
- The Fly apps run in Fly mode and `make fly-status` passes ([fly.md](fly.md)).
- `.env.fly` exists (`push_fly_secrets.py init` was run for the first
  deploy).

## Cutover, in this order

Nobody uses `<domain>` yet, so the order only has to avoid locking out
Cloudflare: enable origin pulls in Cloudflare (step 4) before nginx starts
requiring them (step 11).

1. **Origin certificate.** SSL/TLS → Origin Server → Create Certificate.
   Let Cloudflare generate the key; hostnames `<domain>` and
   `*.<domain>`; the longest validity offered. Save the certificate as
   `origin.pem` and the key as `origin.key`, outside the repository (the
   key is shown once).
2. **Origin-pull CA.** Download Cloudflare's certificate authority for
   Authenticated Origin Pulls (zone-level), as Cloudflare's documentation
   for that feature gives it, and save it as `origin-pull-ca.pem`. For the
   stronger per-hostname variant (your own CA, so only your zone can reach
   the origin), use that CA here instead; see "Residual risks".
3. **Stage the three secrets** (values go to flyctl on stdin):

   ```sh
   uv run python scripts/push_fly_secrets.py origin-tls \
     --cert origin.pem --key origin.key --ca origin-pull-ca.pem
   uv run python scripts/push_fly_secrets.py push --app hotel-curator
   ```

   It loads the files the way nginx will: a key that does not belong to
   the certificate, a certificate and CA given the wrong way round, an
   encrypted or OpenSSH key, or a CA equal to the certificate is refused.
   They go into `.env.fly` (mode 0600, git-ignored). To replace them
   later, run `origin-tls` again with `--replace`. Delete `origin.key`
   once it is in `.env.fly`.
4. **SSL/TLS settings**, all in the zone:
   - SSL/TLS encryption mode: **Full (strict)**. The Origin Certificate is
     trusted by Cloudflare for exactly this.
   - Origin Server → **Authenticated Origin Pulls: on**.
   - Edge Certificates: **Always Use HTTPS: on**; **Minimum TLS Version:
     1.2**; **Automatic HTTPS Rewrites: on**; TLS 1.3 on.
   - Edge Certificates → **HSTS: off**. nginx sends the one HSTS value
     (`max-age=86400`); a second one from Cloudflare would make two.
5. **A dedicated IPv4 for the web app** (billed by Fly monthly):

   ```sh
   uv run python scripts/deploy.py setup --edge cloudflare
   ```

   Cloudflare mode is a TCP passthrough service, which Fly does not route
   on a shared IPv4 (ADR 011, assumption A14). The check in step 12 probes
   every address `hotel-curator.fly.dev` resolves to; if a shared IPv4
   still shows up there and fails, release it with
   `flyctl ips release <address> --app hotel-curator`.
6. **DNS**, both records proxied (orange cloud):

   | Type | Name | Target |
   |---|---|---|
   | CNAME | `@` | `hotel-curator.fly.dev` |
   | CNAME | `www` | `hotel-curator.fly.dev` |

   Cloudflare flattens the CNAME at the apex. Do not add a Fly
   certificate for `<domain>` (`flyctl certs add`): Fly does not terminate
   TLS in Cloudflare mode.
7. **Nothing may change the page.** These features add scripts or
   rewrite HTML, which the strict CSP (`script-src 'self'`, no inline
   scripts) would block or which would change what nginx sent:
   - Speed → Optimization: **Rocket Loader off**; Cloudflare Fonts off.
   - Scrape Shield: **Email Address Obfuscation off**; Server-side
     Excludes off.
   - Web Analytics: automatic setup (the injected beacon) **off**.
   - Zaraz: off.
   - Rules → Transform Rules → Managed Transforms: "Add security headers"
     **off** (nginx sends them; this would add a second set).
8. **Redirect www to the apex.** Rules → Redirect Rules → new rule:
   when hostname equals `www.<domain>`, redirect dynamically to
   `concat("https://<domain>", http.request.uri.path)`, status **301**,
   preserve the query string.
9. **Security settings.**
   - WAF: keep Cloudflare's Free Managed Ruleset on (the default).
   - Rate limiting rule (Security → WAF → Rate limiting rules): when the
     URI path equals `/api/v1/analyze`, count per IP; more than 20
     requests in 10 seconds → block for 10 seconds. Add "and the method
     is `POST`" only if the plan offers that field; a `GET` there is a
     cheap 405 anyway. This stops a burst before it reaches Fly; the
     API's own limit (30 a minute per client) still applies behind it.
     Use what the plan allows if these numbers are not offered.
   - **Bot Fight Mode: off.** Decision: on the Free plan it challenges
     every non-browser client and cannot exempt a path, so it would block
     the API's JSON clients and the deploy workflow's smoke test. The WAF,
     the rate limiting rule and the API's own limits cover abuse instead.
   - Security Level: medium (the default).
   - **`/api/docs` stays public.** Decision: it is read-only, served by
     the API from a pinned copy under a narrow CSP, and describes a public
     API. Neither the API nor the rule above limits it (the API's limits
     cover `/v1` only; the security review lists the docs and the OpenAPI
     schema as an accepted risk), so a flood of it is left to Cloudflare's
     WAF and DDoS protection.
10. **Caching.** Caching level Standard; Browser Cache TTL "Respect
    existing headers"; no Cache Rules. nginx already sends what each path
    needs: hashed `/assets/` files `public, max-age=31536000, immutable`,
    pages `no-cache`, API answers and every error `no-store`. A deploy
    needs no purge: new assets have new names and pages are revalidated.
11. **Deploy the web app in Cloudflare mode:**

    ```sh
    uv run python scripts/deploy.py deploy web --edge cloudflare --domain <domain>
    ```

    From now on every web deploy needs `--edge cloudflare --domain
    <domain>` (or `make fly-deploy FLY_DEPLOY_ARGS="--edge cloudflare
    --domain <domain>"`); `deploy.py` refuses a web deploy that would
    silently go back to Fly mode.
12. **Check the result** (see [Check the result](#check-the-result)).

## Check the result

```sh
python scripts/check_edge.py <domain>
```

It must report no FAIL. It checks:
- the home page comes through Cloudflare with nginx's CSP unchanged, one
  HSTS value, a request id and no injected script;
- `www` redirects to the apex and `http` to `https`;
- `/api/readyz` is ready with hotels and a model;
- a hashed asset keeps its one-year cache lifetime;
- at every address `hotel-curator.fly.dev` resolves to, the origin
  refuses the handshake for its own name and answers 400 for `<domain>`
  without Cloudflare's client certificate. An address that cannot be
  reached fails, and so does a 403 (that would mean only the IP check, not
  the certificate, is keeping the probe out);
- TLS 1.1 is refused (SKIP if the machine running the check cannot
  offer TLS 1.1 at all).

Then update every link that still names `hotel-curator.fly.dev`, which no
longer serves the site.

## Rolling back to Fly mode

In this order, so the site never stops answering:

1. `flyctl certs add <domain> --app hotel-curator` and the same for
   `www.<domain>`. `flyctl certs show <domain> --app hotel-curator` lists
   the `_acme-challenge` CNAME Fly needs for a DNS challenge; add it in
   Cloudflare as **DNS only**, and wait until both certificates are
   issued. (The proxied records cannot pass Fly's HTTP challenge.)
2. `uv run python scripts/deploy.py deploy web --edge fly`. Cloudflare in
   Full (strict) accepts Fly's certificate, so the site keeps answering
   through Cloudflare. In this state nginx keys rate limits on
   `Fly-Client-IP`, which is now a Cloudflare address, so visitors share
   buckets; do not stay here.
3. Set both DNS records to **DNS only** (grey cloud) so browsers reach
   Fly directly, and turn Authenticated Origin Pulls off.

## Residual risks

- **Other Cloudflare zones.** With zone-level origin pulls, Cloudflare's
  client certificate proves "came through Cloudflare", not "came through
  this zone". Another Cloudflare customer could point a zone at the origin
  and send its own requests. nginx narrows this: it answers only
  `<domain>` and `www.<domain>` (421 otherwise) and only from Cloudflare's
  ranges. Per-hostname origin pulls with your own CA close it, where the
  plan offers them.
- **Cloudflare's ranges change.** `web/nginx/cloudflare-ips.conf` is
  compared with Cloudflare's published list every week by the
  `cloudflare-ips` workflow; a new range fails it before visitors from
  that range are refused.
