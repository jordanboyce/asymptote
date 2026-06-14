# Roadmap

Forward-looking work for the general-purpose (`master`) Asymptote tool. Shipped items are not listed — check the git log. Financial-analysis roadmap items live on the `fintech` branch.

## Guiding frame

Asymptote owns the trustworthy data layer; the intelligence layer lives upstream in whatever LLM the user trusts. Our job: make a user's own documents and tabular data faithfully agent-queryable, redact PII before any external model call, and never be silently wrong. Reached through Asymptote's own chat UI by default, or an external MCP client when the user prefers one.

## Library — ingestion & context

- **Git repository indexing** — crawl local or remote repos and preserve directory structure as metadata.
- **Watch-folder sync** — auto-index new files dropped into a designated local directory.
- **Richer spreadsheet handling** — multi-sheet workbooks and more complex table structures beyond flat CSVs.

## Notebook — synthesis

- **Multi-document synthesis** — answers that explicitly cross-reference several sources rather than retrieving isolated chunks.
- **Side-by-side source attribution** — clicking a citation scrolls the original document to the exact highlighted passage.
- **Thematic collections** — group documents into notebooks with their own shared Expertise (research frameworks).

## Toolbox — extensibility

- **MCP expansion** — let external agents trigger re-indexing and metadata updates, not just read documents.
- **PWA** — installable progressive web app as the next step beyond plain web.

## Always-on constraints

- Every tool response is PII-redacted before it leaves the process.
- Raw data passes through by default; semantic layers exist only where Asymptote itself must act deterministically (aggregations, type coercion).
- When inference fails, degrade to raw-data tools and let the LLM handle semantics — never guess and pretend.
