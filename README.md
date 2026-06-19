# The Ledger

**Where every U.S. federal dollar comes from and where it goes — drawn entirely from the government's own books.**

🔗 **Live site: [federal-ledger.pages.dev](https://federal-ledger.pages.dev/)**

An interactive, single-page look at the United States federal budget for the latest complete fiscal year (FY2025): $5.23T in, $7.01T out, a $1.78T deficit, and a live national-debt clock. Every figure reconciles to the dollar against the published totals.

## What's inside

- **The shortfall** — revenue vs. outlays vs. deficit, and the live $39T debt clock.
- **Where it goes** — spending by agency (toggle to per-household), with the biggest agency split into Medicare vs. Medicaid.
- **Where it comes from** — receipts by source, plus the spike in tariff revenue.
- **A decade of shifts** — which categories grew, and which shrank as a share of the budget.
- **Cost of debt** — net interest now exceeds the military budget, with a slider for "what if rates stay high?"
- **Who cashes the checks** — top federal contractors, colored by sector (defense / health / services).
- **Where it lands, by state** — two honest lenses: *contracts* (what Washington buys) vs. *grants* (what's returned to the states).
- **Who we owe** — foreign holders of the debt, color-coded by sovereign reserves vs. financial-center custody.
- **Debt vs. the economy** — federal debt as a share of GDP, near post-WWII highs.

## Data sources (all public, no API keys)

- **U.S. Treasury — [fiscaldata.treasury.gov](https://fiscaldata.treasury.gov)** — Monthly Treasury Statement (outlays & receipts), Debt to the Penny, interest expense, average interest rates.
- **[USAspending.gov](https://www.usaspending.gov)** — federal contracts & grants, by recipient and by state.
- **U.S. Treasury — Treasury International Capital (TIC)** — foreign holders of the debt.
- **[FRED](https://fred.stlouisfed.org) / St. Louis Fed** — debt & deficit as a share of GDP.
- **OMB Historical Tables** — Medicare (budget function 570).
- **U.S. Census Bureau** — households & population.

## Rebuild the data

```bash
python3 scripts/build-ledger-data.py   # writes data.json
```

The builder validates that the agency lines sum to total outlays, that receipts − outlays equals the reported deficit, and that the named figures reconcile to the published totals — no guessed numbers.

## Note

The budget figures are reported straight from the Treasury and are non-partisan. The framing and the "Leads" section are editorial — pointers for further reporting, not neutral fact.

## License

Code: [MIT](LICENSE). The underlying data is U.S. government public-domain.
