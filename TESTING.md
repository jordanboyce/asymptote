# Finn — Tester Guide

You have a build of Finn, a privacy-preserving data layer for financial
advisors. This guide gets you running in about 15 minutes and tells you what to
try, what is already known to be broken, and how to report what you find.

**What you are testing is one workflow:** prep a client meeting, record it,
write it up — on a real brokerage export, without anything private reaching an
external AI model. Everything else is in service of that.

---

## 1. Setup

> **If you were sent a URL instead of these instructions, skip this section.**
> Finn is already running; sign in with the one-time code emailed to you and go
> to §3.

**Prerequisites:** Python 3.11+, Node 18+ (only if you plan to rebuild the UI),
and about 2 GB of disk for the model cache on first run.

```bash
git clone <repo-url> finn && cd finn
cp .env.example .env          # then edit — see §2, you must add an API key
./run.sh                      # macOS / Linux
run.bat                       # Windows
```

`run.sh` / `run.bat` create the virtualenv, install dependencies, generate a
self-signed dev certificate, and start the server. First start is slow — it
downloads the embedding model.

**The port is 8000 by default.** The script prints the actual URL on startup;
trust that line over anything else you read. If you set `PORT` in `.env`, the
script picks it up. Some internal notes mention 8473 — that is one developer
machine's override, not the default.

Open the URL the script printed. You should get the Finn UI with an onboarding
screen.

---

## 2. You must supply an AI API key

**Finn ships with no AI credentials.** Chat, and anything that phrases an
answer, will fail until you provide one. This is deliberate — Finn never ships
with a key that bills someone else — but it does mean the app is half-inert on
first launch.

Two options, in order of preference:

1. **Paste a key in the UI** — Settings → AI Provider. Anthropic or OpenAI both
   work. This is the path most testers should use, and it is the path we most
   want feedback on.
2. **Put it in `.env`** if you prefer not to paste into a browser.

**What works with no key at all** — and is worth trying first, because it is
the part of Finn that does not depend on a model:

- Uploading and ingesting files
- The **Trust Report** (what Finn concluded about your file and why)
- **Meeting prep** (`/prep`) — every line is computed, no model involved
- Portfolio metrics, tax-loss scans, IPS drift
- The **Boundary Report** (what left the machine, and what it was replaced with)

---

## 3. The 30-minute pass

Do these in order. This is the product.

**1. Land a messy file.** Create a new client (the UI calls them Collections in
some places — same thing, a rename is in progress) and upload a brokerage
export of your own. A CSV or Excel positions/holdings file is ideal. Do not
clean it up first — the entire point is that you should not have to.

**2. Read the Trust Report.** Open it from the document. It should name which
column is market value, which is cost basis, and how it decided. Tell us if it
got a column wrong, or if it claims confidence you would not give it.

**3. Ask it something.** In chat: "What's the total market value?" then "What
are the top five positions?" Then something it should refuse to guess at.

**4. Prep a meeting.** Fill in a client profile (risk tolerance, target
allocation, any prohibited holdings) and run meeting prep. You should get a
priority-ordered agenda and a **Gaps** section naming everything prep could not
determine. The gaps section is a feature — an empty one on incomplete data is a
bug worth reporting.

**5. Record something.** Record or upload a short audio file of a mock client
conversation. It should transcribe and produce structured notes with action
items.

**6. Watch the boundary.** Open the Boundary Report. It shows what actually
crossed to the external model, with the real value beside the stand-in. Check
that a client name you uploaded does not appear in the "what the AI saw"
column.

---

## 4. Two things worth trying deliberately

These use files in [tests/fixtures/ingest/](tests/fixtures/ingest/) and
demonstrate the specific failure Finn exists to prevent. Upload each to a
**new, empty** client.

**`messy_unrealized_gl.csv`** — the total market value is **$207,727.45**. Two
restricted rows hold text (`See contract`, `Priced monthly`) instead of a
number, which is enough to make a naive tool read the column as text and report
**$203.00** — off by $207,524. Ask Finn for the total.

> The correct behaviour is that Finn **refuses to give you the number** and
> explains why, rather than reporting $203 with a caveat.
>
> **If it gives you $203 — with or without a footnote — that is the single most
> valuable bug you can report.** Quote exactly what it said.

**`opaque_columns.csv`** — the headers are `Col_1` through `Col_7` and mean
nothing. Finn should still identify most of them, using arithmetic (quantity ×
price = market value) rather than column names, and show its reasoning in the
Trust Report.

---

## 5. Known issues — please don't report these

Current as of 2026-08-12.

| Issue | Detail |
|---|---|
| **"Report issue" button returns an error** | The in-app feedback button needs a mail credential that is not configured. Use email instead — see §6. |
| **Onboarding may prompt for a key even in Basic Mode** | The keyless path is not fully verified end to end. |
| **Excel files with two-row or merged headers** | Not handled. Columns will come through wrong. Known gap, on the roadmap. |
| **Two tables stacked on one sheet** | Only the first is read. |
| **Wide-format reports** (periods as columns) | Not handled. |
| **Trust Report cannot distinguish "header confirmed" from "header assumed"** | When it reports the header is row 1, that may mean it verified it, or it may mean it gave up. Known. |
| **Documents ingested by an older build** | Their Trust Report omits the vendor-match line. Re-upload to get a complete report. |
| **`/mcp` endpoint** | Developer surface. Not part of what you are evaluating. |
| **Electron desktop shell** | Legacy, unmaintained. Use the browser. |

**Do report** anything where Finn gives you a **confidently wrong number**.
That is the failure mode this product exists to prevent, and it outranks every
other category of bug.

---

## 6. Reporting

Email **jordan.boyce@cyberlion.dev**. The in-app button does not work yet (§5).

Useful reports include:

1. What you did, in enough detail to repeat
2. What you expected, and what you got
3. **The file, if you can share it** — a messy real export is the most valuable
   thing you can send. If you cannot share it, a description of its shape
   (which columns, what the odd values look like, which vendor produced it) is
   still useful. Do not send anything containing real client data unless you
   have cleared it.

Screenshots of the Trust Report or the Boundary Report are especially useful,
because they show Finn's own reasoning rather than just its output.

---

## 7. Your data

- **Everything stays on your machine**, in `./data` beside the app. Uploads,
  extracted text, embeddings, and the audit log are all local files.
- **When you use chat, redacted text goes to the AI provider you configured**
  (Anthropic or OpenAI, under your own key and their terms). Names, emails, and
  account numbers are replaced before the request leaves. The Boundary Report
  shows you exactly what crossed — check it rather than taking this paragraph
  on faith.
- **Nothing is sent to us automatically.** No telemetry, no usage reporting. We
  learn what you found only when you tell us.
- To delete everything, stop the app and delete the `data/` directory.

**A caution for this stage:** treat this as pre-release software. Prefer
anonymized or sample data over live client files until you are satisfied with
what §6 shows you.
