# Billing v2 — example spec

This is a sample `SPEC.md` that goes with `tasks.example.json`. A real spec is written during
`/autopilot-plan` and records every decision up front, so unattended workers never have to ask.

## Goal
Replace the subscription-only billing module with invoice-based billing.

## Decisions
- **D1** One invoice per billing period per customer.
- **D2** Money is stored as integer cents, never floats.
- **D3** Invoices are soft-deleted (`deleted_at`), never removed.

## Design
### Data model
- `Invoice(id, customer_id, period_start, period_end, status, total_cents, deleted_at)`
- `InvoiceLine(id, invoice_id, description, quantity, unit_cents)`

### Service
`createInvoice`, `finalizeInvoice` and `voidInvoice`. Finalized invoices are immutable.

## Out of scope
Payment collection, tax calculation, PDF rendering.
