# The Dug Out consignment portal

One web app with two sides:

- **The shop side** (owner and staff) runs consignment from intake to payout.
- **The consignor portal** lets each consignor see their own items, prices, sales, statement and payouts, submit new items with photos, and approve or counter price changes.

It is self-contained in this folder so it can move to its own repository whenever you like.

**Setting it up for real:** start with [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md), a click-by-click guide covering GitHub, hosting on Render, your domain, Square, eBay, and security and backups. [docs/HOSTING_OPTIONS.md](docs/HOSTING_OPTIONS.md) compares hosting choices on price, ease and security.

## What it does

| Area | What happens |
|---|---|
| **Consignors** | Contact details, payout method and details (Zelle, PayPal, Venmo, check, ACH, cash, store credit), an optional personal commission rate, notes, and a portal sign-in link you text or email to them. They accept your consignment agreement in the portal before they submit items. |
| **Intake** | Check an item in with the card details (year, set, card #, player, grader, grade, cert #), quantity, asking price, the consignor's floor, case or shelf, and photos (phone camera works). Each item gets a SKU such as `DO000042` that never changes. Consignors can also submit items from the portal; they show on the dashboard until you check them in or decline them. |
| **Pricing, both ways** | Set a price directly, or send it to the consignor to approve. Consignors can ask for a different price. Each item keeps its price history. A sale below the consignor's floor is refused unless you tick that the consignor approved it. |
| **Labels** | Printable barcode labels for a thermal printer (2.25 × 1.25 in: Zebra, Dymo, Rollo) or Avery 5160 sheets. The barcode (Code 128) is the item's SKU, which is also its SKU in Square, so scanning it at the register rings up that item. The **Scan / SKU** box at the top of every staff page jumps to an item when you scan its label with a USB scanner. |
| **Square** | With a Square access token, each item becomes a Square item with that SKU, price and a stock count of 1. In-store sales come back automatically (webhook) or with **Pull in-store sales**, and are split like any other sale. When an item sells on eBay or Whatnot, or goes back to the consignor, its Square stock goes to 0 so it can't be sold twice. Without a token you can still download a Square item import file. |
| **eBay (direct)** | Connect the shop's eBay seller account once, then list an item (or a batch) with one click: photos, card details, the right category, and eBay's required Graded (grader, grade, cert) or Ungraded (card condition) details, using your shipping, payment and return policies. A price change updates the live listing. Paid eBay orders come back as sales with eBay's actual fee. When an item sells anywhere else or is returned, its eBay listing ends by itself. |
| **Whatnot, TCGplayer (and eBay by file)** | Every item page has ready-to-paste listing copy (an eBay-length title built from the card details, and a description). For many items, tick them on Inventory and download that marketplace's bulk upload file, with photo links included. |
| **Sales** | Record where it sold, the price, the actual marketplace fee (blank fills in the estimate for that channel) and the order number. Commission comes from the item's rate, else the consignor's, else your tiers by sale price. The shop's and consignor's shares always add up to the cent. The owner can void a sale (returns, cancelled orders); the item goes back up for sale. |
| **Two accounts** | Sales land in the **shop operating account**. Each consignor's share belongs in the **consignment trust account**, and payouts come only from there. The dashboard and the Accounts page show what the trust account should hold (everything owed to consignors), what it does hold, and the exact transfer to make. Record the transfer and it balances. |
| **Payouts** | A hold period (default 7 days) covers returns and chargebacks before money is payable. The Payouts page lists who can be paid and how much. Record a payout as sent (with the check number or confirmation) or as pending to send later. |
| **Charges and credits** | Grading fees, shipping you paid for a consignor, corrections. They show on the consignor's statement. |
| **Statements** | Each consignor's statement lists every sale (price, fee, commission %, commission, their share), charge, credit and payout, with a running balance. They can view, print and download it. |
| **Aging** | Each item's consignment period (default 90 days) is tracked; the dashboard lists items near or past the end so you can reprice them with the consignor or return them. |
| **Records** | An activity log of every change (who and when), CSV downloads of sales and inventory, and staff accounts (owner and staff). Staff can do the daily work; only the owner can change settings, manage staff, or void sales and payouts. |

## Running it

```bash
cd dugout-consign
pip install -e ".[dev]"
python -m consign serve            # http://127.0.0.1:8100
python -m consign serve --share    # let the shop's other computers and phones on the Wi-Fi open it
```

The first visit asks you to create the owner account, then opens **Settings**. Check the commission tiers, fee estimates, hold period and consignment period before adding anyone.

**Try it with sample data first:** `python -m consign demo` on an empty database adds two consignors, items, sales, a payout and a price change to approve. It prints the sign-ins (owner and one consignor) so you can see both sides.

### Settings (environment variables)

| Setting | Purpose |
|---|---|
| `CONSIGN_DATA_DIR` | Where the database and photos are kept (default `./data`). Back this folder up. |
| `DATABASE_URL` | A PostgreSQL connection; used instead of the built-in SQLite database when set. |
| `CONSIGN_BASE_URL` | The portal's public address, e.g. `https://consign.dugouttx.com`. Used in sign-in links and in the photo links sent to eBay and Whatnot. |
| `CONSIGN_ENV=production` | HTTPS-only cookies; needs `CONSIGN_SECRET_KEY` and `CONSIGN_SETUP_TOKEN` (asked for when creating the owner). |
| `SQUARE_ACCESS_TOKEN`, `SQUARE_LOCATION_ID` | Turn on the Square connection. `SQUARE_ENVIRONMENT=sandbox` uses Square's test site. |
| `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL` | Accept Square's webhook so register sales arrive within seconds. The Square page shows the URL to register. |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_RUNAME` | Turn on the eBay connection (production keyset). `EBAY_ENVIRONMENT=sandbox` uses eBay's test site. |
| `CONSIGN_SYNC_MINUTES` | How often Square and eBay sales are pulled in automatically (default 15; 0 turns it off). |

### Putting it online

See [docs/HOSTING_OPTIONS.md](docs/HOSTING_OPTIONS.md) for the comparison, and Parts 1–3 of [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for the steps. In short: Render, Starter plan with a 5 GB disk (about $8.25 a month), set up from the included `render.yaml`, at `consign.dugouttx.com`. Square and eBay setup are Parts 5 and 6.

Owner locked out? On the server run `python -m consign reset-password owner@example.com` and open the link it prints. The owner can download a full backup (database and photos) from Settings at any time.

### Marketplace upload files

The eBay file uses eBay's bulk upload format (Seller Hub > Reports > Uploads), in the Sports Trading Card Singles category (CCG Individual Cards for Pokémon and other TCGs), with item specifics filled in. The Whatnot file follows Whatnot's bulk listing layout, and the TCGplayer file follows the Seller Portal pricing import, which needs each card's TCGplayer product id (there's a field for it on the item). Marketplaces change their templates now and then, so upload a single item first; if a column is rejected, the column names are in `consign/listings.py` and are easy to change. Set your eBay shipping, return and payment policy names in that file to fill those columns automatically.

eBay listings can also go straight through eBay's API (the eBay page). Whatnot's seller API is limited and TCGplayer no longer issues new API keys, so those two stay with upload files.

## Tests

```bash
python -m pytest -q
```

The tests cover the money splits, balances and account reconciliation, the barcode encoder (decoded back and checked), the Square and eBay connections against fake Square and eBay servers, the upload files, and the web pages, including that a consignor cannot reach any other consignor's records or any shop page.
