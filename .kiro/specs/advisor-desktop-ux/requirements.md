# Requirements Document

## Introduction

Asymptote is a locally-running desktop application for financial advisors. It ingests brokerage exports (CSV/XLSX/PDF), transcribes meeting recordings, and provides AI-powered chat and portfolio analysis — all with PII redacted before any data leaves the machine.

This spec covers the onboarding and workflow polish needed to make Asymptote genuinely usable by a pilot group of 5–6 non-technical financial advisors at a Pershing/NetX360 custodian firm. The goal is a tool any advisor can pick up on day one and use confidently for their three core workflows: meeting prep, portfolio analysis, and meeting notes.

The application already has working backends for document ingestion, semantic search, AI chat, portfolio metrics, and audio transcription. This spec focuses on the user-facing experience that connects those capabilities into coherent, advisor-friendly workflows.

---

## Glossary

- **Advisor**: A licensed financial advisor using Asymptote on their own machine. The person interacting with the application.
- **Client**: The end investor whose portfolio data the Advisor manages. The Client never interacts with Asymptote directly.
- **Collection**: A logical grouping of documents in Asymptote — typically one Collection per Client household (e.g., all of the Henderson household's brokerage exports and meeting transcripts).
- **Household**: One Client relationship, possibly spanning multiple brokerage accounts and individuals.
- **Meeting Brief**: A structured, one-page pre-meeting document summarizing a Client's portfolio — household total, account breakdown, top positions, tax-loss candidates, concentration alerts, and cash drag.
- **Note of Record**: A compliance-ready summary of a client meeting, drafted from the meeting transcript and portfolio context.
- **Onboarding**: The first-run experience that takes an Advisor from a fresh install to a working, configured application.
- **AI Provider**: An external LLM service (Anthropic Claude, OpenAI, Ollama Cloud) that Asymptote sends redacted context to for chat and analysis.
- **API Key**: A credential that authenticates Asymptote to an AI Provider. Stored locally on the Advisor's machine.
- **Basic Mode**: The simplified interface mode showing only the features a non-technical advisor needs for daily workflows.
- **Expert Mode**: The full interface mode exposing advanced configuration, developer tools, and MCP settings.
- **PII Redaction**: The process of detecting and replacing personally identifiable information (names, account numbers, SSNs, etc.) before any data is sent to an external AI Provider.
- **Pilot**: The initial group of 5–6 advisors testing Asymptote before broader rollout.

---

## Requirements

### Requirement 1: First-Run Onboarding

**User Story:** As a financial advisor installing Asymptote for the first time, I want a clear, step-by-step setup experience, so that I can get from a fresh install to a working tool without needing technical help.

#### Acceptance Criteria

1. WHEN an Advisor launches Asymptote for the first time with no AI Provider configured, THE Application SHALL display a guided onboarding flow before showing the main interface.
2. THE Onboarding Flow SHALL present setup in a sequence of no more than four steps: (1) welcome and privacy explanation, (2) AI Provider selection and API key entry, (3) first Collection creation, (4) completion confirmation.
3. WHEN the Advisor enters an API key during onboarding, THE Application SHALL validate the key against the selected AI Provider before advancing to the next step.
4. IF an API key validation fails during onboarding, THEN THE Application SHALL display a specific error message identifying whether the failure was due to an invalid key, a network error, or an unsupported provider.
5. THE Onboarding Flow SHALL explain, in plain language visible on the privacy step, that all Client data stays on the Advisor's machine and that PII is redacted before any data is sent to the AI Provider.
6. WHEN the Advisor completes onboarding, THE Application SHALL land them in the main interface with their first Collection active and the Chat tab visible.
7. WHERE the Advisor skips onboarding, THE Application SHALL allow access to the main interface and surface a persistent, dismissible prompt to complete AI Provider setup.
8. THE Onboarding Flow SHALL be completable without requiring the Advisor to read any external documentation.

---

### Requirement 2: AI Provider Configuration

**User Story:** As a financial advisor, I want to configure my AI provider once and have it work reliably, so that I don't have to think about API keys or model selection during my workday.

#### Acceptance Criteria

1. THE Settings Panel SHALL support configuration of at least Anthropic Claude and OpenAI as AI Providers in Basic Mode.
2. WHEN an Advisor saves an API key, THE Application SHALL validate the key and display a clear success or failure indicator within 10 seconds.
3. THE Application SHALL store API keys locally on the Advisor's machine and SHALL NOT transmit API keys to any server other than the configured AI Provider's authentication endpoint.
4. WHEN a configured AI Provider's API key becomes invalid (e.g., expired or revoked), THE Application SHALL display a clear, actionable error message the next time the Advisor attempts to use AI features.
5. THE Settings Panel SHALL display the currently active AI Provider and model in Basic Mode without requiring the Advisor to navigate to Expert settings.
6. WHERE the Advisor has not configured any AI Provider, THE Application SHALL display a visible prompt in the Chat tab directing the Advisor to Settings to complete setup.

---

### Requirement 3: Collection and Client Setup

**User Story:** As a financial advisor, I want to create a collection for each client household and upload their brokerage exports, so that I can run analysis and chat against that client's data.

#### Acceptance Criteria

1. THE Application SHALL allow an Advisor to create a named Collection from the Collections view with a single action.
2. WHEN an Advisor uploads a file to a Collection, THE Application SHALL accept CSV, XLSX, and PDF formats and display upload progress.
3. WHEN an Advisor uploads a Pershing or NetX360 export file, THE Application SHALL automatically detect the file format and ingest it without requiring the Advisor to configure column mappings.
4. WHEN a file upload completes successfully, THE Application SHALL display a confirmation showing the file name and the number of records or pages ingested.
5. IF a file upload fails, THEN THE Application SHALL display an error message identifying the file and the reason for failure (unsupported format, parse error, or empty file).
6. THE Application SHALL allow an Advisor to upload multiple files to a Collection in a single session without navigating away.
7. WHEN a Collection contains ingested brokerage data, THE Application SHALL display a summary of the data available (number of positions, accounts, and date of most recent export) in the Collection detail view.

---

### Requirement 4: Meeting Prep — Generate Meeting Brief

**User Story:** As a financial advisor preparing for a client meeting, I want to generate a one-page meeting brief from the client's uploaded portfolio data, so that I can replace 45 minutes of manual report assembly with a single action.

#### Acceptance Criteria

1. WHEN a Collection contains at least one ingested brokerage export with detected financial roles, THE Application SHALL display a "Generate Meeting Brief" action prominently in the Collection view.
2. WHEN the Advisor triggers "Generate Meeting Brief", THE Application SHALL produce a structured brief within 60 seconds.
3. THE Meeting Brief SHALL include all of the following sections when the underlying data supports them: household total market value, account-by-account breakdown, top 10 positions by market value, tax-loss candidates (positions with unrealized loss above the configured threshold), concentration alerts (positions exceeding the configured percentage of household value), and cash drag alerts (uninvested cash above the configured threshold).
4. THE Meeting Brief SHALL display the date and source file(s) used to generate it so the Advisor can verify the data is current.
5. WHEN a section of the Meeting Brief cannot be computed (e.g., no cost basis data available for tax-loss analysis), THE Application SHALL display a clear explanation of why that section is absent rather than omitting it silently.
6. THE Meeting Brief SHALL be printable as a single page from the browser's print function without requiring additional formatting steps.
7. WHEN the Advisor adjusts alert thresholds (tax-loss minimum, concentration percentage, cash drag minimum), THE Application SHALL regenerate the affected sections of the Meeting Brief immediately.
8. THE Meeting Brief SHALL be generated entirely from locally stored data and SHALL NOT require an active AI Provider connection.

---

### Requirement 5: Portfolio Analysis via Chat

**User Story:** As a financial advisor, I want to ask natural-language questions about a client's portfolio during or after a meeting, so that I can answer client questions quickly without switching to a spreadsheet.

#### Acceptance Criteria

1. WHEN an Advisor sends a message in the Chat tab with a Collection active, THE Application SHALL respond using the portfolio data and documents in that Collection as context.
2. THE Chat Interface SHALL be accessible in Basic Mode with no more than two navigation actions from the main screen.
3. WHEN the Chat responds with portfolio data (positions, values, percentages), THE Application SHALL present the data in a structured format (table or list) rather than prose only.
4. WHEN the Chat uses a tool to query structured portfolio data, THE Application SHALL display a brief indicator of which tool was called so the Advisor can understand the source of the answer.
5. THE Chat Interface SHALL support the following question types without requiring the Advisor to use special syntax: position lookup by ticker or name, account balance queries, sector allocation breakdown, unrealized gain/loss for a position or account, and cash position summary.
6. WHEN the Chat cannot answer a question from the available data (e.g., the question requires market data not in the Collection), THE Application SHALL clearly state what data is missing rather than providing a speculative answer.
7. THE Chat Interface SHALL maintain conversation context across at least 10 consecutive messages within a session so the Advisor can ask follow-up questions without restating context.
8. WHEN the Advisor switches between Collections, THE Chat Interface SHALL clear the conversation history and load the context for the newly selected Collection.

---

### Requirement 6: Meeting Recording and Transcription

**User Story:** As a financial advisor, I want to record a client meeting and have it transcribed automatically, so that I have a searchable record of the conversation without manual note-taking.

#### Acceptance Criteria

1. THE Application SHALL provide a recording control (start/stop) accessible from the main interface during an active Collection session.
2. WHEN the Advisor starts a recording, THE Application SHALL display a visible recording indicator so the Advisor knows the recording is active.
3. WHEN the Advisor stops a recording, THE Application SHALL begin transcription automatically and display progress.
4. WHEN transcription completes, THE Application SHALL save the transcript to the active Collection and display a confirmation with the transcript length (duration and word count).
5. THE Application SHALL support upload of a pre-recorded audio file (MP3, M4A, WAV) as an alternative to live recording, with the same automatic transcription behavior.
6. THE Transcription Engine SHALL run locally on the Advisor's machine and SHALL NOT send audio data to any external service.
7. WHEN a transcript is saved to a Collection, THE Application SHALL make it immediately searchable via the Chat interface.
8. IF transcription fails (e.g., unsupported audio format or corrupted file), THEN THE Application SHALL display an error message identifying the file and the reason for failure.

---

### Requirement 7: Post-Meeting Note of Record

**User Story:** As a financial advisor, I want to generate a compliance-ready note of record after a client meeting, so that I can complete my documentation in minutes rather than writing it from scratch.

#### Acceptance Criteria

1. WHEN a Collection contains at least one meeting transcript, THE Application SHALL offer a "Draft Note of Record" action.
2. WHEN the Advisor triggers "Draft Note of Record", THE Application SHALL generate a structured note using the most recent transcript and the Collection's portfolio context.
3. THE Note of Record SHALL include: a summary of topics discussed, any action items or decisions mentioned in the transcript, and a reference to the PII redaction audit log confirming that no identifiable data was sent to the AI Provider.
4. THE Note of Record SHALL be editable by the Advisor before saving or exporting.
5. WHEN the Advisor saves a Note of Record, THE Application SHALL store it in the active Collection and make it searchable.
6. THE Note of Record generation SHALL use PII-redacted transcript content when calling the AI Provider, and THE Application SHALL display the redaction summary alongside the generated note.

---

### Requirement 8: Basic Mode Usability

**User Story:** As a non-technical financial advisor, I want the Basic Mode interface to show only what I need for my daily workflows, so that I can use the tool confidently without being overwhelmed by technical options.

#### Acceptance Criteria

1. WHEN the Application is in Basic Mode, THE Interface SHALL display only the following navigation items: Collections, Chat, Documents, and Settings.
2. WHEN the Application is in Basic Mode, THE Settings Panel SHALL show only AI Provider configuration and the Basic/Expert mode toggle; all developer, MCP, OCR, and system configuration options SHALL be hidden.
3. THE Basic/Expert mode toggle SHALL be accessible from the main header in both modes so the Advisor can switch without navigating to Settings.
4. WHEN the Application is in Basic Mode, THE Chat Interface SHALL not expose raw tool-call output, model selection dropdowns, or reranking toggles; these SHALL be replaced by a simple "thinking…" indicator while the AI processes.
5. WHEN the Application is in Basic Mode, THE Collections view SHALL surface the "Generate Meeting Brief" and "Draft Note of Record" actions as primary buttons rather than requiring the Advisor to discover them through menus.
6. THE Application SHALL persist the Advisor's mode preference (Basic or Expert) across sessions.
7. WHEN an Advisor in Basic Mode encounters an error, THE Application SHALL display a plain-language explanation and a suggested next action rather than a technical error code or stack trace.

---

### Requirement 9: Privacy and Compliance

**User Story:** As a financial advisor at a regulated firm, I want assurance that client data never leaves my machine unprotected, so that I can use the tool without violating compliance obligations.

#### Acceptance Criteria

1. THE Application SHALL redact PII from all content sent to an external AI Provider before transmission, using the Presidio-based redaction engine already implemented in `services/privacy/`.
2. THE Application SHALL maintain a local audit log of every redaction event, recording entity type, replacement token, source document, and timestamp.
3. WHEN the Advisor requests it, THE Application SHALL display the redaction audit log for the current session in a human-readable format.
4. THE Application SHALL display a persistent, visible indicator in the Chat interface confirming that PII redaction is active.
5. THE Application SHALL NOT store API keys, Client data, or redaction audit logs in any location accessible outside the Advisor's local machine.
6. WHEN the Advisor reviews a generated document (Meeting Brief, Note of Record), THE Application SHALL display a summary of what PII categories were detected and redacted during generation.
7. THE Application SHALL support a "dry run" mode that shows the Advisor exactly what will be redacted from a document before any AI call is made.
8. THE Application SHALL function for all local operations (document ingestion, Meeting Brief generation, search) without an active internet connection; only AI chat and AI-assisted drafting require connectivity to the AI Provider.

---

### Requirement 10: Pilot Readiness

**User Story:** As the product team running a pilot with 5–6 advisors, I want the application to be stable and self-sufficient enough that advisors can use it independently, so that the pilot produces meaningful feedback without requiring constant support.

#### Acceptance Criteria

1. THE Application SHALL complete first-run onboarding without requiring the Advisor to edit configuration files, run terminal commands, or contact technical support.
2. WHEN the Application encounters an unrecoverable error, THE Application SHALL display a recovery action (restart, clear cache, or contact support) rather than leaving the Advisor on a blank or broken screen.
3. THE Application SHALL retain all Collections, documents, and settings across application restarts without data loss.
4. WHEN a new version of the Application is available, THE Application SHALL notify the Advisor with a non-blocking banner and provide a one-click update path.
5. THE Application SHALL support a single-user, single-machine install model; no shared server, database, or network configuration SHALL be required for the pilot.
6. THE Application SHALL be installable on Windows 10 or later and macOS 12 or later from a standard installer package without requiring administrator privileges beyond the initial install.
7. WHEN the Advisor has not used the Application for 7 or more days, THE Application SHALL display a brief reminder of the primary workflows on next launch to reduce re-onboarding friction.
