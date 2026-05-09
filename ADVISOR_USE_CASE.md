# Finn — Your 30-Second Meeting Brief

**How one button replaces 45 minutes of meeting prep**

*A pilot walkthrough for your practice — April 2026*

---

## The Problem

Your team doesn't have a data problem. You have a **data assembly** problem.

Before every client meeting, someone pulls Pershing reports, flattens NetX360 exports in Excel, cross-checks the math, and assembles a picture of where the household stands. That process takes 45 to 60 minutes per client. It's not analysis — it's data entry.

And it's where errors creep in. A transposed number. A negative that Excel read as text. A lot that got counted twice. By the time you're sitting across from your client, you're trusting math that nobody double-checked.

## The Solution: One Button, One Brief

Finn replaces that entire process with a **single button press**.

Upload your client's brokerage exports — Pershing, Schwab, Fidelity, Vanguard, NetX360. Finn detects the format, strips the preamble rows, fixes the accounting negatives, rolls up multi-lot positions, and builds a structured data layer underneath.

Then hit **"Generate Meeting Brief."** In 30 seconds, you get a one-page summary that would have taken your assistant an hour to assemble:

- **Household snapshot** — Total value across all accounts, with change since last review
- **Account-by-account breakdown** — IRA, TOD, Joint, Variable Annuity, each with current value and P&L
- **Top positions** — Largest holdings by market value with weight and sector
- **Tax-loss harvesting candidates** — Every position underwater by more than your chosen threshold, with unrealized loss and holding period
- **Concentration alerts** — Any ticker that's grown past 10% of the household
- **Cash drag** — Uninvested cash above $50K across accounts
- **Recent filings** — SEC 8-K or earnings events on positions your client holds (coming soon)

The brief isn't a chat response. It's a **document** — structured, printable, ready to walk into the meeting with. The opportunity flags aren't a separate screen. They're sections of the brief, highlighted because they matter for today's conversation.

---

## The Walkthrough: Prepping for the Henderson Review

*It's 9:00 AM. You have a quarterly review with Bob and Linda Henderson at 10:00.*

### 9:00 AM — Generate the Brief

You uploaded the Henderson Pershing exports last week. You open Finn, select the Henderson collection, and hit Generate Meeting Brief.

30 seconds later, you're looking at:

- **Total household:** $1.2M across 3 accounts (up 2.1% since last review)
- **TOD account:** down $408, driven by PKST which is underwater 5.3% on cost
- **Tax-loss flag:** 3 positions with unrealized losses over $500 — PKST, XYZ Corp, and DEF Holdings
- **Concentration flag:** Apple at 11.2% of household (IPS limit: 10%)
- **Cash drag:** $62,000 uninvested in the joint account
- **New position:** 200 shares of VTI added to the IRA since last meeting

That's the same work your assistant does in Excel. Except Finn doesn't miss the PKST loss, doesn't transpose a number from NetX360, and doesn't forget to check the variable annuity.

### 10:00 AM — During the Meeting

Before the conversation starts, you hit the **microphone button** in Finn. With the Hendersons' permission, the tool records the meeting and transcribes it in real time using Whisper — all running locally on your machine.

The brief covers 90% of what you need. But Bob asks: *"What's happening with that biotech stock we bought last year?"*

This is where the **chat** comes in — not as the primary tool, but as the safety net for questions the brief didn't anticipate. You type the question, get the position details and P&L in seconds, and keep the conversation moving.

Linda asks about sector allocation. One question. Finn breaks it down, shows tech at 34% versus the 25% target, and lists the positions driving the overweight.

Meanwhile, the transcript is being saved to the Henderson collection. When the meeting ends, you have a complete, searchable record of the conversation — ready for compliance review or to inform the next meeting's brief.

**Privacy note:** Every response — whether in the brief, the chat, or the transcript — is PII-redacted before any AI model sees it. The raw transcript stays local. If you need to send it through AI for summarization or note generation, Presidio scrubs it first. You can review the full redaction audit log at any time.

### 11:00 AM — After the Meeting

The meeting is over. Now comes the part everyone dreads: documentation.

But you have something your assistant never had: a **full transcript** of the conversation, already saved to the Henderson collection, plus every data point that was pulled up during the meeting. Finn combines both to draft a **Note of Record**:

> *"Discussed the PKST downtrend with the Hendersons; client agreed to rebalance into sector-equivalent ETFs. Flagged concentration risk in AAPL at 11.2% vs. 10% IPS limit. Redaction audit logs attached to confirm PII handling."*

You review it, edit if needed, and file it. Instead of spending 20 minutes writing it from scratch.

Then: *"Draft a follow-up email to Bob and Linda."* Finn writes a personalized summary of the discussion points and next steps, PII-safe, ready for you to review and send.

---

## The Bottom Line

| Task | Today | With Finn |
|------|-------|----------------|
| Pull and flatten brokerage reports | 30–45 min | Automatic on upload |
| Assemble meeting prep | 15–20 min | One button, 30 seconds |
| Check for underwater positions / risks | 15–20 min in Excel | Baked into the brief |
| Cross-reference SEC filings | Manual web search | Included in brief (coming soon) |
| Draft compliance notes | 20–30 min writing | Auto-generated (coming soon) |
| Draft follow-up email | 10–15 min | One command (coming soon) |
| **Total per client** | **75–110 minutes** | **Under 5 minutes** |

That's **practice leverage**. Your assistant gets 5 hours back per week. A junior team member using Finn can surface insights — tax-loss candidates, concentration violations, 8-K impacts — that typically require a senior analyst. The tool doesn't replace your judgment. It eliminates the grunt work that prevents you from exercising it.

---

## Why Finn vs. Claude for Excel?

Anthropic just shipped Claude for Excel, generally available. It's powerful — Citadel and Hg are using it for coverage models and due diligence — and it's built for analysts who already work in Excel all day.

Finn solves a different problem.

**You don't have a clean-Excel-workbook problem. You have a brokerage-export problem.**

Pershing's Unrealized Gain/Loss CSV has 9 preamble rows and accounting negatives in parens. NetX360 ships hierarchical multi-account reports with insurance metadata blocks between every account header. Schwab quotes percentages as text strings. None of this lands cleanly in Excel — your assistant pastes it, fixes the math by hand, and hopes nothing got transposed. Claude for Excel can read what's in the spreadsheet, but it can't fix what was wrong on the way in.

Finn's vendor profiles know every one of those formats. Drop the file in, get a typed, queryable Holdings table out. The advisor never sees a preamble row.

**Your client's account number doesn't belong in someone else's API logs.**

Claude for Excel sends cell contents to Anthropic's API in cleartext. That's fine for IB analysts working with public filings. It's not fine for advisors handling real client SSNs, account numbers, and balances. Finn's PII redaction layer catches 31+ entity types — including financial-specific patterns like brokerage account numbers and CUSIPs in client-name context — and replaces them before any external call. The original lives on your machine; only the redacted version reaches the model. Full audit log of every replacement.

**A wash-sale check needs the household, not a workbook.**

When the Hendersons' joint account harvests a loss in PKST, Finn checks whether Bob's IRA, Linda's Roth, or the trust bought PKST in the last 30 days. Per-workbook tools — including Excel agents — miss spousal IRA traps because they only see one workbook at a time. Finn's Collection model is the household, and every cross-account analysis runs across all of it.

**Local-first by default.**

Finn runs on your machine. Documents, indexes, transcripts, audit logs — all local. External AI is opt-in per request, BYO key, and goes through the redaction layer first.

For Citadel-shaped institutions running coverage models against FactSet feeds, Claude for Excel is the right tool. For an advisor walking into a Schwab review with a household's worth of messy custodian data, Finn is the layer below it.

---

## Privacy: Nothing Leaves Your Machine Unprotected

We built Finn with one non-negotiable rule: nothing identifiable reaches any AI model without being scrubbed first.

- **Your data stays local.** The AI only sees redacted versions of your documents.
- **31+ entity types detected.** SSNs, account numbers, names, addresses, emails, phone numbers, financial identifiers — all caught and replaced.
- **Full audit log.** Every redaction is recorded: what was found, what it was replaced with, confidence score. Reviewable at any time.
- **Preview before sending.** A dry-run tool shows you exactly what will be redacted before any data leaves your machine.
- **You control the sensitivity.** Add allow-list entries for fund names or tickers. Adjust detection thresholds per client collection.

---

## What's Ready vs. What's Coming

| Capability | Status | Timeline |
|-----------|--------|----------|
| Brokerage ingestion (5 vendors + NetX360) | ✓ Live | Now |
| Numeric accuracy (currency, negatives, rollups) | ✓ Live | Now |
| PII redaction + audit log + preview | ✓ Live | Now |
| Document search (semantic + keyword) | ✓ Live | Now |
| Portfolio metrics + generic aggregation | ✓ Live | Now |
| Meeting recording + transcription (Whisper) | ✓ Live | Now |
| One-click Meeting Brief | Building | 2 weeks |
| In-app chat (BYO API key) | Next | 3–4 weeks |
| Compliance Note of Record | Planned | 5–6 weeks |
| Follow-up email drafting | Planned | 5–6 weeks |
| Proactive morning dashboard | Planned | 7–8 weeks |

---

## Your Feedback Shapes What We Build Next

This document shows where Finn is headed. Your input directly determines what ships first. Please be candid — the more specific, the better.

1. **The meeting brief is our top priority.** If you could only have one output from this tool, is that the right one? Or would you rather start with something else — the compliance notes, the opportunity flags, the chat?

2. **What did we miss in the Henderson walkthrough?** We described the morning from 9am to 11:30am. What does your team actually do that we left out?

3. **What would compliance need to see?** The PII redaction audit log is designed for exactly this. What else would your compliance officer want before approving this tool?

4. **Desktop, phone, or both?** Would you use this at your desk, between meetings on your phone, or does it need to work in both places?

5. **How many clients per week get a deep review?** This helps us understand the scale of time savings. If the answer is 15 clients at 45 minutes each, that's 11+ hours of prep your team does every week.

6. **The big question:** If Finn could give your assistant 5 hours of their week back by automating the report-flattening and meeting-brief process, *where would you have them focus that extra time to grow your AUM?*

---

*Thank you for being part of the pilot. Your feedback is what makes this tool worth building.*
