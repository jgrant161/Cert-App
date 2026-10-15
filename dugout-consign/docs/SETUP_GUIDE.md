# Setup guide: from zero to a live portal

This walks you through every click, in order. Plan on about **2–3 hours** spread over a couple of days: eBay can take up to a business day to approve a new developer account, and new web addresses can take a few hours to start working.

Websites change their menus now and then. If a button has a slightly different name, look for the closest match. If you get stuck, copy the exact error message and send it to me.

**What it costs:** about **$8.25 a month** for hosting on Render ([why Render](HOSTING_OPTIONS.md)). GitHub, Square's API and eBay's API are free, and you already own the domain.

**You'll need:**
- The GitHub account (`jgrant161`).
- A credit card for Render.
- Your GoDaddy login (for `dugouttx.com`).
- The shop's Square login (the owner account).
- The shop's eBay seller login (`thedugoutspring`).
- About 20 minutes per part.

| Part | What you do | Time |
|---|---|---|
| 1 | Give the portal its own GitHub repository | 15 min |
| 2 | Put it online with Render | 20 min |
| 3 | Give it the address consign.dugouttx.com | 15 min + waiting |
| 4 | First sign-in and shop settings | 20 min |
| 5 | Connect Square | 25 min |
| 6 | Connect eBay | 40 min + approval wait |
| 7 | Lock it down and back it up | 15 min |

---

## Part 1 — Give the portal its own GitHub repository

The portal was built inside the Cert-App repository. This gives it a home of its own. (Prefer not to? Tell me "move it for me" and I'll create the repository and copy everything over, history included.)

**A. Create the new, empty repository**
1. Go to https://github.com and sign in.
2. Top right, click the **+** button → **New repository**.
3. **Owner:** jgrant161. **Repository name:** `dugout-consign`.
4. Choose **Private**.
5. Leave "Add a README", ".gitignore" and "license" **unticked/None**, so it starts empty.
6. Click **Create repository**. Leave this page open.

**B. Download the portal's files**
1. In a new tab, go to https://github.com/jgrant161/Cert-App.
2. Click the branch button near the top left (it shows a branch name) and pick **claude/awesome-brahmagupta-8033j8**.
3. Click the green **Code** button → **Download ZIP**.
4. Open your Downloads folder, right-click the zip → **Extract All** → **Extract**.
5. Open the extracted folder, then open the **dugout-consign** folder inside it. You should see `consign`, `docs`, `tests`, `.github`, `README.md`, `pyproject.toml` and `render.yaml`.

**C. Upload them**
1. Back on the empty `dugout-consign` page from step A, click the link **uploading an existing file**.
2. In the dugout-consign folder, press **Ctrl+A** to select everything (including `.github` and `.gitignore`), then drag it all onto the GitHub page.
3. Wait until every file shows in the list (about 65 files), then click **Commit changes**.
4. Check the repository's main page: `render.yaml`, `pyproject.toml` and the `consign` folder should be at the top level, not inside another folder. If you see a single `dugout-consign` folder instead, delete the repository (**Settings** → bottom of the page → **Delete this repository**) and redo step C using the files *inside* that folder.

**D. Tidy up Cert-App.** The portal only lives on that one Claude branch of Cert-App; it was never added to Cert-App's main code. Once the new repository works, tell me and I'll remove the `dugout-consign` folder from that branch so Cert-App is back to only the certificate app.

---

## Part 2 — Put it online with Render

1. Go to https://render.com → **Get Started** → **GitHub**. Sign in with GitHub and click **Authorize Render**.
2. If Render asks you to create or choose a **workspace**, pick the free personal/**Hobby** option. (If only paid workspace plans are offered, stop here and check with me first.)
3. In the Render dashboard, click **+ New** (top right) → **Blueprint**.
4. Under "Connect a repository," click **Configure GitHub** (or **Connect GitHub**). On the GitHub page that opens, choose **Only select repositories** → pick **dugout-consign** → **Install** (or **Save**).
5. Back in Render, click **Connect** next to `jgrant161/dugout-consign`.
6. **Blueprint Name:** `dugout-consign`. Render reads the `render.yaml` file and lists one **web service** (Starter plan) with a **5 GB disk**. Click **Apply** (or **Deploy Blueprint**).
7. Add your card when asked.
8. Click the **dugout-consign** service, then **Logs**. The first build takes 3–5 minutes. It's ready when the log says **Your service is live** (or the status shows **Live**).
9. At the top of the service page is its web address, something like `https://dugout-consign.onrender.com`. Copy it somewhere: you need it in Part 3.

**Get your one-time setup code**
1. In the service's left menu, click **Environment**.
2. Find **CONSIGN_SETUP_TOKEN**, click the eye icon (or **Reveal**) and copy the value.

**Create the owner account**
1. Open your `onrender.com` address. The portal shows **Set up the portal**.
2. Enter your name and email, a password of at least 10 characters (a long one), and the setup code.
3. Click **Create owner account**. You land on Settings. Leave them for Part 4.

---

## Part 3 — Give it the address consign.dugouttx.com

Your main website stays on Netlify and your email isn't touched; this only adds a new sub-address.

**A. Tell Render about the address**
1. Render → **dugout-consign** service → **Settings** → scroll to **Custom Domains** → **+ Add Custom Domain**.
2. Type `consign.dugouttx.com` → **Save**.
3. Render shows a **CNAME** record to create, pointing to your `onrender.com` address (for example `dugout-consign.onrender.com`). Leave this page open.

**B. Add the record at GoDaddy**
1. Go to https://dcc.godaddy.com/domains and sign in.
2. Click **dugouttx.com** → **DNS** (sometimes **Manage DNS**).
3. Click **Add New Record** (or **Add**).
4. **Type:** CNAME. **Name:** `consign`. **Value:** your onrender address *without* `https://` (for example `dugout-consign.onrender.com`). **TTL:** leave as is.
5. Click **Save**. Don't change any other record. MX and TXT records run your email, and the `@` and `www` records run the main website.

**C. Finish in Render**
1. Back on Render's Custom Domains section, click **Verify**. It can take from a few minutes to a few hours. Render then issues the HTTPS certificate by itself.
2. When `https://consign.dugouttx.com` opens the portal, go to **Environment** → **+ Add Environment Variable**:
   - **Key:** `CONSIGN_BASE_URL` **Value:** `https://consign.dugouttx.com`
3. Click **Save, rebuild, and deploy** (or **Save Changes**). Wait for **Live**.

From now on, always use `https://consign.dugouttx.com`.

**Optional: a link from the main website.** Ask me to add a "Consignor login" button to the Sell page of the Netlify site.

---

## Part 4 — First sign-in and shop settings

1. Sign in at https://consign.dugouttx.com.
2. **Settings** (top menu):
   - **Commission tiers.** The defaults are only placeholders: 30% under $100, 25% from $100, 20% from $500, 15% from $1,000. Enter your real terms as `price:percent`, separated by commas.
   - **Take fees off before the split.** Ticked means marketplace and card fees come off the sale price before commission. Untick it if the shop absorbs fees.
   - **Fee estimates.** These only pre-fill new sales. Check them against your current Square, eBay, Whatnot and TCGplayer rates.
   - **Consignment period** (default 90 days) and **hold before payout** (default 7 days).
   - **Account names and opening balance.** Use the names of your two bank accounts. For the opening balance, enter what the consignment account holds today: $0 if you're opening a new one.
   - **Label stock.** Thermal printer or Avery 5160.
   - **Agreement.** Read the consignment agreement text and change it to your terms. Consignors must accept it before they can submit items.
   - Click **Save settings**.
3. **Staff:** Settings → **Staff accounts** → add each employee as **Staff**. Copy the link it shows and text it to them; they set their own password.
4. **Practice run:**
   1. Add a consignor called "Shop Test".
   2. **Check in an item** with a photo and a $1 price.
   3. **Print label** on your label printer. In the print box choose the label printer, set scale to 100%, and turn off headers and footers.
   4. Record a pretend sale, then void it (owner only, from the item page).

---

## Part 5 — Connect Square

This makes consigned items appear in your Square item library and brings register sales back into the portal by themselves.

**A. Create the Square app and copy two values**
1. Go to https://developer.squareup.com and click **Sign in**. Use the shop's normal Square login.
2. Open the **Developer Console** (or **Applications**) and click **+** / **Create your first application**. Name it `Dug Out Consignment` and click **Save**.
3. Click the new application to open it.
4. At the top of the page, switch from **Sandbox** to **Production**.
5. Left menu → **Credentials**. Next to **Production Access token**, click **Show** and copy it. Treat it like a password: it can read and change your Square account.
6. Left menu → **Locations**. Copy the **Location ID** of the shop (a code like `L8ABC123XYZ`).

**B. Put them on the server**
1. Render → dugout-consign → **Environment** → **+ Add Environment Variable**, and add:
   - `SQUARE_ACCESS_TOKEN` = the token from step 5
   - `SQUARE_LOCATION_ID` = the location id from step 6
2. **Save, rebuild, and deploy**. Wait for **Live**.

**C. Turn on automatic sales**
1. In the Square developer app (still on **Production**): left menu → **Webhooks** → **Subscriptions** → **Add subscription**.
2. **Name:** `Dug Out Consignment`.
3. **URL:** `https://consign.dugouttx.com/webhooks/square`
4. **API version:** leave the default.
5. **Events:** tick `order.updated` and `payment.updated` (type them in the search box).
6. Click **Save**. Open the subscription you just made and copy its **Signature key**.
7. Back in Render → **Environment**, add:
   - `SQUARE_WEBHOOK_SIGNATURE_KEY` = the signature key
   - `SQUARE_WEBHOOK_URL` = `https://consign.dugouttx.com/webhooks/square` (exactly as typed in step 3)
8. **Save, rebuild, and deploy**.

**D. Test it**
1. Portal → **Square** → it should say **Connection: On**. Click **Send new items to Square**.
2. Square Dashboard → **Items** → **Item library**: your consigned items are there, named as in the portal, with SKUs like `DO000001` and a stock of 1.
3. On the register, scan the $1 test item's label. It rings up. Take the $1 in cash.
4. Within a minute or two, the test item shows as **Sold** in the portal, with the sale on the dashboard. If it doesn't, click **Pull in-store sales now** on the Square page. The portal also checks by itself every 15 minutes.
5. Undo the test: void the sale in the portal, and refund it in Square.

If the Square token ever leaks, go to Credentials → **Replace** to get a new one, then update `SQUARE_ACCESS_TOKEN` in Render.

---

## Part 6 — Connect eBay

This lets you list items on eBay with one click (photos, card details, graded/ungraded condition) and brings eBay sales back automatically. When an item sells anywhere else, its eBay listing ends by itself.

**A. Prepare your eBay seller account (in Seller Hub)**
1. Sign in to eBay as the shop (`thedugoutspring`) → **My eBay** → **Selling** (Seller Hub) → **Account** (or the gear menu) → **Business policies**.
2. Make sure you have one of each. Create them if not:
   - **Shipping policy**, for example "Cards – Ground Advantage", with your handling time, ship-from ZIP 77373 and costs.
   - **Payment policy** (immediate payment).
   - **Return policy**, for example 30-day returns or "no returns".

**B. Join the eBay Developers Program** (one time; approval can take up to a business day)
1. Go to https://developer.ebay.com → **Register** (or **Join**).
2. Fill in the form with the shop's email and click **Join**. Confirm the email eBay sends you. This is a separate developer login, not your seller login.

**C. Create your keys**
1. Once approved, sign in at https://developer.ebay.com. Click your name at the top → **Application Keysets** (or **Application Access Keys**).
2. **Application Title:** `DugOutConsign` → in the **Production** column, click **Create a keyset**.
3. eBay asks about **Marketplace Account Deletion** notifications. Choose the option to **opt out / exempt** because you **do not persist eBay user data**. That's accurate: the portal keeps only your own order numbers, prices and fees, never buyers' names or addresses. If asked for a reason, write: "Single-seller inventory tool; stores only the seller's own listing and order totals, no eBay user data." Submit.
4. Back on the keyset page, the Production keys appear. Copy:
   - **App ID (Client ID)**
   - **Cert ID (Client Secret)**: treat it like a password.

**D. Tell eBay where to send you back after you approve the connection**
1. On the same page, next to the Production App ID, click **User Tokens**.
2. Under **Get a Token from eBay via Your Application**, click **Add eBay Redirect URL**.
3. Fill in:
   - **Display Title:** `The Dug Out Consignment`
   - **Privacy Policy URL:** `https://consign.dugouttx.com/privacy`
   - **Auth Accepted URL:** `https://consign.dugouttx.com/ebay/callback`
   - **Auth Declined URL:** `https://consign.dugouttx.com/ebay`
4. Make sure **OAuth** is selected or enabled (there may be a radio button or checkbox labelled **OAuth Enabled**). Click **Save**.
5. eBay shows an **RuName** (eBay Redirect URL name) on that row: a long name like `Harrisons_Cards-DugOutCo-DugOut-abcdefgh`. Copy it exactly. It is *not* a web address.

**E. Put the keys on the server**
1. Render → **Environment** → add:
   - `EBAY_CLIENT_ID` = App ID
   - `EBAY_CLIENT_SECRET` = Cert ID
   - `EBAY_RUNAME` = the RuName
2. **Save, rebuild, and deploy**. Wait for **Live**.

**F. Connect and choose your policies**
1. Portal → **eBay** (top menu) → **Connect eBay**.
2. eBay's sign-in page opens. Sign in with the **seller** account (`thedugoutspring`) → click **Agree** (or **I agree**).
3. You land back on the portal's eBay page: "eBay is connected."
4. Under **Listing settings**, pick your **Shipping**, **Payment** and **Return** policies. Check the **Ships from** address, then click **Save**.

**G. Test with one item**
1. Make a test item with a photo and a high price (for example $999) so no one buys it. Fill in grader and grade if it's graded, or condition if it's raw.
2. On the item page, click **List on eBay**. Then click the **on eBay** link to see the live listing.
3. Click **End on eBay** to take it down.

If eBay refuses a listing, the portal shows eBay's exact message (for example a missing item detail). Send it to me and I'll adjust the listing.

**Good to know**
- The connection lasts about **18 months**. The eBay page shows a "reconnect by" date; just click **Connect eBay** again before then.
- Orders are pulled in every 15 minutes, or click **Pull eBay orders now**. The sale uses eBay's actual fee for that order.
- To list many at once: **Inventory** → tick items → **List on eBay (direct)** → **Go**. Or on the eBay page: **List every for-sale item not yet on eBay**.
- Whatnot and TCGplayer still use the upload files (Inventory → tick items → upload file), since neither offers an open API to new sellers.

---

## Part 7 — Lock it down and back it up

1. **Two-step verification** on every account that can reach the portal or its keys:
   - GitHub: profile picture → Settings → Password and authentication
   - Render: Account Settings → Security
   - Square, eBay and GoDaddy: their account security pages
   - **The email account** behind all of them. This one matters most.
2. **Backups:** Render snapshots the disk daily and keeps at least a week of snapshots (service → **Disks**). Once a month, also click Settings → **Download a backup** in the portal and save the .zip to OneDrive or Google Drive.
3. **People leave:** on Staff accounts or the consignor's page, click **Turn off**.
4. **Keys:** the Square token, eBay secret and setup code live only in Render's Environment page. Never email or text them. If one leaks, replace it at Square or eBay, then update Render.
5. **Updates:** when I change the code, Render redeploys by itself within a few minutes. Your data on the disk is kept.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| Render build failed | Open **Logs**, copy the last 30 lines and send them to me. |
| "This link has expired" for a staff or consignor invite | Create a new link from their page (links work once, for 14 days). |
| consign.dugouttx.com doesn't open yet | DNS can take a few hours. In GoDaddy, check the CNAME's name is exactly `consign` and the value has no `https://` and no `/` at the end. |
| Square page says "Not connected" | Check the two Square keys are in Render's Environment, then **Save, rebuild, and deploy**. Make sure the token is the **Production** one. |
| Register sales don't appear | Click **Pull in-store sales now**. If that works, the webhook isn't. Check `SQUARE_WEBHOOK_URL` matches the subscription URL exactly, and that the signature key is the Production subscription's. |
| eBay: "eBay did not finish connecting" | You clicked Decline, or the Auth Accepted URL in eBay doesn't exactly match `https://consign.dugouttx.com/ebay/callback`. |
| eBay: "invalid redirect_uri" on eBay's page | `EBAY_RUNAME` must be the RuName text, not a web address. |
| eBay refuses a listing | The portal shows eBay's reason. Fix the item (for example the grader's short name: PSA, BGS, SGC, CGC) and click **List on eBay** again, or send me the message. |
| Forgot the owner password | Render → dugout-consign → **Shell** → type `python -m consign reset-password you@youremail.com` and press Enter. Open the link it prints and choose a new password. |
