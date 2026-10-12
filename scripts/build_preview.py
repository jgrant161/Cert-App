"""Build the read-only online preview: every screen of the running app, as one clickable page.

    python scripts/seed_demo.py --demo-users && python scripts/seed_portal_demo.py
    python -m certapp serve --port 8795          # in another window
    python scripts/build_preview.py              # writes build/certificate-review-demo.html

Expects the sample client as client #1 and the vendor-portal sample as client #2 (a fresh data folder).
"""

import re
import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "certificate-review-demo.html"
OUT.parent.mkdir(exist_ok=True)
c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, follow_redirects=True)
r = c.post("/login", data={"email": "demo.admin@insighttaxadvisors.com", "password": "demo-password-1"})
assert "Sign out" in r.text, "login failed"

def main_of(path):
    page = c.get(path).text
    return re.search(r"<main>(.*)</main>", page, re.S).group(1)

TABS = ["schedule", "followup", "certificates", "customers", "excluded", "ask", "policy", "batches"]
views = {"home": main_of("/")}
for t in TABS:
    views[t] = main_of(f"/e/1?tab={t}")
for cid in sorted({int(x) for x in re.findall(r'href="/e/1/c/(\d+)"', views["certificates"])}):
    views[f"c{cid}"] = main_of(f"/e/1/c/{cid}")
views["portal"] = main_of("/e/2/portal")
views["vendor"] = main_of("/e/2/portal/try")
for rid in sorted({int(x) for x in re.findall(r'href="/portal/r/(\d+)"', views["portal"])}):
    views[f"r{rid}"] = main_of(f"/portal/r/{rid}")

def rewrite(h):
    h = re.sub(r"<script>.*?</script>", "", h, flags=re.S)
    h = re.sub(r'<iframe[^>]*></iframe>',
               '<div class="pdf-placeholder"><b>Certificate PDF</b><span>In the app, the customer\'s PDF opens here, '
               'next to the reading, so the reviewer can check every line against the document. '
               'The sample data has no real PDFs.</span></div>', h)
    h = h.replace('<a class="button ghost" href="/e/1/portal">Vendor portal</a>', '')
    h = h.replace('href="/e/1/export.xlsx"', 'href="#export" data-preview="export"')
    h = re.sub(r'href="/e/1/c/(\d+)"', r'href="#c\1"', h)
    h = re.sub(r'href="\?tab=certificates&(?:amp;)?status=(\w+)"', r'href="#certificates" data-status="\1"', h)
    h = re.sub(r'href="(?:/e/1)?\?tab=(\w+)"', r'href="#\1"', h)
    h = re.sub(r'href="/e/2/portal/try"', 'href="#vendor"', h)
    h = re.sub(r'href="/e/2/portal(?:#\w+)?"', 'href="#portal"', h)
    h = re.sub(r'href="/e/2"', 'href="#portal"', h)
    h = re.sub(r'href="/portal/r/\d+/cert/\d+"', 'href="#" data-preview="cert"', h)
    h = re.sub(r'href="/portal/r/(\d+)"', r'href="#r\1"', h)
    h = re.sub(r'href="/portal"', 'href="#vendor"', h)
    h = re.sub(r'href="/users[^"]*"', 'href="#" data-preview="users"', h)
    h = re.sub(r'href="/e/1"', 'href="#schedule"', h)
    h = re.sub(r'href="/"', 'href="#home"', h)
    h = re.sub(r'href="/e/\d+(\?[^"]*)?"', 'href="#schedule"', h)
    h = re.sub(r'href="mailto:[^"]*"', 'href="#" data-preview="mail"', h)
    h = re.sub(r'<form([^>]*)action="[^"]*"', r'<form\1', h)
    h = h.replace('enctype="multipart/form-data"', '')
    return h

sections = "\n".join(f'<section class="view" id="v-{k}" hidden>{rewrite(v)}</section>' for k, v in views.items())
leftover = re.findall(r'href="(/[^"]*)"', sections)
assert not leftover, leftover[:10]

css = open(ROOT / "certapp" / "web" / "static" / "app.css").read()
light = re.search(r":root \{(.*?)\n\}", css, re.S).group(1)
dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}\s*\}", css, re.S).group(1)
rest = css[css.index("* { box-sizing"):]
theme = (f":root {{{light}\n}}\n@media (prefers-color-scheme: dark) {{ :root:not([data-theme=\"light\"]) {{{dark} color-scheme: dark; }} }}\n"
         f":root[data-theme=\"dark\"] {{{dark} color-scheme: dark; }}\n")
extra = """
.preview-banner { background: var(--blue); border-bottom: 1px solid var(--border); padding: 10px 16px; font-size: 13px;
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; }
.preview-banner b { font-weight: 600; }
.preview-nav { display: flex; flex-wrap: wrap; gap: 6px; }
.preview-nav a { padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); }
.preview-nav a.on { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
.pdf-placeholder { min-height: 60vh; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: flex-start;
  text-align: center; padding: 24px; color: var(--muted); background: var(--bg); border: 2px dashed var(--border); border-radius: 8px; height: 100%; }
.pdf-placeholder b { color: var(--text); font-size: 15px; }
.pdf-placeholder span { max-width: 36ch; }
.toast { position: fixed; left: 50%; transform: translateX(-50%); bottom: calc(20px + env(safe-area-inset-bottom, 0px));
  background: var(--text); color: var(--bg); padding: 10px 16px; border-radius: 8px; max-width: calc(100% - 32px); font-size: 13px; z-index: 10; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
.top .mode { background: rgba(255,255,255,.18); }
"""
script = """
<script>
(function () {
  var views = document.querySelectorAll('.view');
  var navLinks = document.querySelectorAll('.preview-nav a');
  var statusFilter = null;
  function area(key) {
    if (key === 'portal' || key === 'vendor' || /^r\\d+$/.test(key)) return key === 'portal' ? 'portal' : 'vendor';
    return 'review';
  }
  function show() {
    var key = (location.hash || '#schedule').slice(1);
    var target = document.getElementById('v-' + key) || document.getElementById('v-schedule');
    views.forEach(function (v) { v.hidden = v !== target; });
    var a = area(target.id.slice(2));
    navLinks.forEach(function (l) { l.classList.toggle('on', l.dataset.area === a); });
    if (target.id === 'v-certificates') {
      target.querySelectorAll('tbody tr').forEach(function (tr) {
        var pill = tr.querySelector('.pill[class*="status-"]');
        tr.hidden = !!(statusFilter && pill && pill.textContent.trim() !== statusFilter);
      });
    }
    statusFilter = null;
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', show); show();

  var toastEl = document.createElement('div'); toastEl.className = 'toast'; toastEl.hidden = true;
  toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl);
  var timer;
  function toast(msg) { toastEl.textContent = msg; toastEl.hidden = false; clearTimeout(timer);
    timer = setTimeout(function () { toastEl.hidden = true; }, 5000); }

  var MESSAGES = {
    export: 'In the app, this downloads the Excel workbook: Overview, Schedule (with status, validity fields, flags and reviewer notes), summaries, Customers, Follow-Up, Excluded Lines, Reconciliation and Batches.',
    cert: 'In the app, this downloads the signed certificate PDF for the vendor, and the download is logged.',
    users: 'In the app, administrators add vendors and staff on the Users page.',
    mail: 'In the app, this opens an email to the contact.'
  };
  document.addEventListener('click', function (ev) {
    var s = ev.target.closest('[data-status]');
    if (s) { statusFilter = s.dataset.status; if (location.hash === '#certificates') { ev.preventDefault(); show(); } return; }
    var a = ev.target.closest('[data-preview]');
    if (a) { ev.preventDefault(); toast(MESSAGES[a.dataset.preview]); return; }
    var add = ev.target.closest('#add-item');
    if (add) {
      var rows = add.closest('fieldset').querySelector('#item-rows');
      var copy = rows.firstElementChild.cloneNode(true);
      copy.querySelectorAll('input').forEach(function (el) { el.value = ''; el.placeholder = ''; });
      copy.querySelectorAll('select').forEach(function (el) { el.selectedIndex = 0; });
      rows.appendChild(copy);
    }
  });

  document.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var form = ev.target;
    if (form.classList.contains('filter')) {          // schedule search and filters work in the preview
      var q = (form.querySelector('[name=q]').value || '').toLowerCase();
      var st = form.querySelector('[name=state]').value;
      var stat = form.querySelector('[name=status]') ? form.querySelector('[name=status]').value : '';
      var shown = 0;
      form.closest('section').querySelectorAll('tbody tr').forEach(function (tr) {
        var cells = tr.querySelectorAll('td'); if (cells.length < 5) return;
        var ok = (!q || tr.textContent.toLowerCase().indexOf(q) >= 0) && (!st || cells[2].textContent.trim() === st)
          && (!stat || cells[4].textContent.trim() === stat);
        tr.hidden = !ok; if (ok) shown++;
      });
      toast(shown + ' row' + (shown === 1 ? '' : 's') + ' shown.');
      return;
    }
    if (form.id === 'request-form') {
      toast('In the app, the portal checks each item against the client\\'s rules and returns the certificate or the contact. Open request #1 or #2 below to see real results.');
      return;
    }
    toast('This is a read-only preview with sample data. Uploading, overrides, reviews, merges, settings and questions work in the installed app.');
  });
})();
</script>
"""
page = f"""<title>Certificate Review Demo</title>
<style>
{theme}{rest}{extra}
</style>
<header class="top">
  <a class="brand" href="#home">Certificate Review</a>
  <span class="mode">Preview with sample data</span>
</header>
<div class="preview-banner"><span><b>Read-only preview, updated October 12, 2026.</b> All names, numbers and rules are made up.</span>
  <nav class="preview-nav" aria-label="Preview sections">
    <a href="#schedule" data-area="review">Certificate review</a>
    <a href="#portal" data-area="portal">Vendor portal: firm setup</a>
    <a href="#vendor" data-area="vendor">Vendor portal: what vendors see</a>
  </nav></div>
<main>
{sections}
</main>
{script}"""
open(OUT, "w").write(page)
print(len(page), "bytes;", len(views), "views:", " ".join(views))
