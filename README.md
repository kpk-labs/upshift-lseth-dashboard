# LsETH Carry Fund page

Static, self-contained page for KPK's LsETH Carry Fund (on Upshift): LsETH
supplied as collateral on Morpho, USDC borrowed against it, that USDC deployed
into the KPK USDC Morpho vaults. Shows the carry and its formula, the position,
the full rate history, a carry simulator, how KPK manages risk, and the terms.
Layout and design system follow KPK's BTC Carry Fund board.

**Live page:** https://external.kpk.io/staking-carry-fund/lseth/ (copied from this repo every 4 hours by kpk-labs/external-resources)

## How it updates

`index.html` is **generated — do not edit it here.** A GitHub Actions workflow
(`.github/workflows/refresh.yml`) rebuilds it every 4 hours from live data and
commits only when a rate actually moved, so the history stays meaningful rather
than recording one commit per run. Run it on demand from the Actions tab
("Refresh dashboard" → Run workflow).

No secrets or API keys are involved: both upstream sources are public and
keyless.

| Source | Used for |
|---|---|
| `api.morpho.org/graphql` | market borrow rates, vault net APY, position, share-price history |
| `yields.llama.fi/pools` | LsETH staking APY (Liquid Collective pool) |
| public Ethereum RPCs (publicnode, mevblocker, drpc) | one share-price reading per build: the vault's recorded price and the price from KPK's NAV Calculator |

## Files

| File | |
|---|---|
| `index.html` | generated output — the published page |
| `build_dashboard.py` | renders the page; `python3 build_dashboard.py [outfile]` |
| `config.json` | editable copy, links, contracts and the agent settings the page states (health factor manager, risk section, terms); stamped with the date they were set |
| `position_tracker.py` | all Morpho queries and the share-price reading; `--history`, `--start`, `--peers` for ad-hoc reads |
| `share_price.jsonl` | the share-price series behind the chart: backfilled once from the hourly NAV samples in `karpatkey/curation-dune`, then one reading appended per build and committed by the workflow |
| `assets/` | KPK fonts and design tokens (`kpk-fonts.css`, `kpk-tokens.css`), KPK logo and protocol icons (`logos/*.webp`), inlined into the output at build time |
| `robots.txt` | link-reachable, deliberately not search-indexed |

Stdlib only — no `pip install` needed. All assets are inlined, so the published
page makes no external requests at view time.

## Reading the page

- **Carry** is what 1 LsETH earns *in addition to* its own staking yield, net of
  the 10% performance fee. Add it to the staking APY for the total.
- **Spread basis** — "Live" is the forward-looking view; the trailing averages are
  a reality check, each pairing the vault's realized net APY with the borrow rate
  over the same window.
- The **LTV slider** is a projection at a fixed spread. Its shaded segment is the
  health factor manager's band from `config.json`, shown as LTV at the market's LLTV.
- The header's "As of" timestamp is when the data was read. The page does not
  refresh itself in the browser.
