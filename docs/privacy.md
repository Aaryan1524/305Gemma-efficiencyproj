# Privacy and activity data

The tracker identifies a visible, titled, normal-layer window belonging to the
frontmost process. It skips missing and blocked windows and captures only that
window. Pixels go directly to Apple Vision in memory. OCR text stays in a bounded
memory buffer until classification. A crash or exit loses unclassified samples.

The built-in blocklist covers selected password/messaging apps and sensitive title
keywords; see `BLOCKED_APPS` and `BLOCKED_TITLE_WORDS` in focusledger.py. It is not a
complete privacy filter. Sensitive content inside an allowed window can be read.
Local inference and JSON output do not prevent semantic prompt injection.

Goals, session dates/times, category estimates, and model-generated activity notes
persist as JSONL. Summaries can repeat sensitive content. HTML reports contain this
data too. These files are not encrypted by FocusLedger; protect your macOS account
and review reports before sharing. No telemetry or cloud inference is added.
Dependency/model downloads and help links use external services.

Default storage is `~/Library/Application Support/FocusLedger/`: ledger.jsonl,
report.html, report.demo.html, config.json, and onboarding state. The source checkout
contains only the committed synthetic sample. Legacy repository-local ledger.jsonl
is copied if the destination is absent. Originals are preserved, existing destination
history wins, and failures are reported. An explicit FL_LEDGER_PATH disables copying.

New day appends fresh goals without deleting history. At midnight the tracker
finishes the prior day and stops; enter goals again to resume. Reports select one
day, inferring dates for old rows from preceding goals. Delete stored ledgers and
reports, legacy copies, and backups to erase history. Demo generation never reads
personal activity. Voice uses on-device Apple Speech and transient transcript files
that are removed after use; an interrupted process may leave temporary files.
