# Sprint 9.11 — Direct CUPS printing

## Boundary

Labels produces an exact-size, single-label PDF. `LabelPrinterAdapter` is the Printing port;
`CupsPrintingAdapter` is its infrastructure implementation. `VariantLabelPrintService` owns
Catalog coordination; the Intake draft endpoint uses the same adapter for its pre-completion
label. Printing is outside Complete Intake and all Inventory/Catalog/Pricing transactions.

The API Docker image installs Debian `cups-client` only. It does not run cupsd, contain the
macOS/Xprinter driver, access USB, perform printer discovery or persist PrintJobs.

## Configuration

Required runtime environment, disabled and blank in `.env.example`:

```text
PRINTING_ENABLED=false
CUPS_SERVER=
CUPS_USER=
CUPS_PRINTER=
CUPS_IPP_VERSION=1.1
```

The actual LAN address, user and queue exist only in ignored local `.env`. `CUPS_SERVER`
accepts `host:port`, including DNS/mDNS names. There are no infrastructure defaults.

Capability is true only when printing is enabled, fixed command `lp` exists, and server,
user and explicit queue are configured. Discovery is deliberately not consulted. The UI
keeps PDF/system-print actions when capability is false.

## Submission

The controlled argv is:

```text
/usr/bin/lp
-h <CUPS_SERVER>/version=<CUPS_IPP_VERSION>
-U <CUPS_USER>
-d <CUPS_PRINTER>
-n <copies>
-t <Core-owned job name>
<secure temporary PDF path>
```

No shell is used and HTTP cannot supply flags, command, server, user, printer or job name.
Copies must be an integer 1..500. The PDF is produced by Labels and the temporary file is
deleted in `finally`. Timeout is 30 seconds.

Success means `status=submitted`; it does not claim physical printing. The adapter extracts
`request id is <id>` as `external_job_id` when CUPS emits it. Disabled/incomplete config and
missing lp return a sanitized unavailable error. Timeout, network/authentication/unknown queue
and non-zero lp exit return sanitized submission errors. Detailed stderr is server-log-only.
No printing error rolls back business data.

## Acceptance record

- Container: Debian cups-client 2.4.2, no cupsd.
- Docker remote IPP path accepted job `Xprinter_XP_365B-1186` with 1 copy.
- Docker remote IPP path accepted job `Xprinter_XP_365B-1187` with 3 copies.
- User confirmed physical output: first exactly 1 label, then exactly 3 labels; 40×30 size,
  orientation and feed are correct. Direct printing is available from the Core UI.
- Local `/health` returned ok after rebuild.

Automated adapter tests mock subprocess and cover argv, IPP 1.1, job id parsing, copies,
disabled/missing configuration, missing lp, timeout and non-zero failure without requiring a
physical printer. Validation: 361 Python tests and 7 JavaScript tests passed; Ruff, JavaScript
syntax, Alembic head and git diff checks passed; Docker rebuild and `/health` passed. Commit was
not created by this UAT pass.
