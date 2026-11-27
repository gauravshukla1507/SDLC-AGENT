# Business Requirements Document: Expense Claim Policy Engine

| | |
|---|---|
| **Document ID** | BRD-FIN-2026-014 |
| **Version** | 1.0 (test BRD for Agentic SDLC) |
| **Business owner** | Head of Finance Operations |
| **Prepared by** | Delivery team |
| **Date** | 26 Nov 2026 |
| **Status** | Ready for delivery |

---

## 1. Background

Employees submit expense claims (travel, meals, lodging, local transport and so on) through three channels: the
employee portal, the mobile app and a monthly bulk upload from corporate card feeds. Each channel checks claims
against the travel and expense policy differently. Finance rejects about 18% of claims manually, usually for missing
receipts, over-limit meals or late submissions, and employees receive inconsistent answers.

Finance wants **one policy engine** that every channel calls. It validates a claim against the policy, explains every
violation in plain language, and decides who must approve it.

## 2. Business objectives

| ID | Objective | Measure of success |
|----|-----------|--------------------|
| OBJ-1 | Apply the same policy decision in every channel | 100% of channels call the engine within one quarter |
| OBJ-2 | Reduce manual rejections by Finance | Manual rejections fall from 18% to below 5% |
| OBJ-3 | Give employees clear, actionable feedback | Every rejection lists each violated rule with a reason |
| OBJ-4 | Route approvals correctly the first time | Zero claims approved by the wrong approval level |

## 3. Scope

**In scope**
- Validating a single expense claim (a header plus one or more expense lines) against the policy rules in section 6.
- Calculating approved and rejected amounts per line and for the whole claim.
- Determining the approval route (manager, finance, or CFO) from the approved total.
- Detecting duplicate lines within a claim.
- Clear, machine-readable and human-readable results.

**Out of scope**
- User interface, storage of claims, notifications, and payment or reimbursement.
- Live currency rates. Rates are passed in with the claim.
- OCR or receipt image validation. The engine only knows whether a receipt is attached.

## 4. Stakeholders

| Role | Interest |
|------|----------|
| Head of Finance Operations (owner) | Policy is applied consistently; fewer manual rejections |
| Employees | Fast, clear decisions on their claims |
| Line managers, Finance controllers, CFO office | Receive only the claims they must approve |
| Internal audit | Every decision is explainable and reproducible |
| Portal, mobile and batch channel teams | A simple, stable interface to call |

## 5. Definitions

| Term | Meaning |
|------|---------|
| Claim | One submission by one employee: claim id, employee id, employee grade, submission date, base currency (INR), and lines |
| Line | One expense: line id, date, category, amount, currency, city tier (for meals and lodging), receipt attached (yes or no), merchant, and optional notes |
| Approved amount | The part of a line the policy allows, in INR |
| Violation | A rule a line or claim breaks, with a rule id, severity (`reject` or `warn`) and a message |

## 6. Business rules

All amounts are converted to **INR** with the exchange rates supplied in the claim, rounded **half-up to 2 decimals**,
before any limit is applied.

| Rule | Description |
|------|-------------|
| BR-01 Categories | Allowed categories: `travel`, `lodging`, `meals`, `local_transport`, `client_entertainment`, `office_supplies`. Any other category is rejected. |
| BR-02 Receipt | A receipt is required for any line above **₹500** (after conversion). A line above ₹500 without a receipt is rejected in full. |
| BR-03 Meals limit | Meals per person per day: tier-1 city **₹1,500**, tier-2 **₹1,000**, tier-3 **₹750**. Amounts above the limit are capped at the limit; the excess is rejected with a `warn` violation, and the line stays partly approved. |
| BR-04 Lodging limit | Lodging per night by employee grade: grades **G1–G3 ₹6,000**, **G4–G5 ₹9,000**, **G6+ ₹15,000**. The excess is capped and warned, as in BR-03. |
| BR-05 Submission window | Lines dated more than **60 days** before the submission date are rejected (`late submission`). A line dated after the submission date is rejected (`future date`). |
| BR-06 Duplicates | Two lines in the same claim with the same date, category, amount and merchant (case-insensitive) are duplicates. The first is evaluated normally; later duplicates are rejected. |
| BR-07 Client entertainment | Requires the `notes` field to name the client. Without notes the line is rejected. |
| BR-08 Currency | A line in a currency with no supplied exchange rate is rejected (`missing exchange rate`). |
| BR-09 Approval routing | Based on the claim's **total approved amount**: up to **₹25,000** → manager; above ₹25,000 and up to **₹1,00,000** → finance controller; above ₹1,00,000 → CFO. A claim with nothing approved → `no approval needed (fully rejected)`. |
| BR-10 Claim validity | A claim needs at least one line. The claim id, employee id and grade are required. An invalid claim is rejected as a whole with a clear error and no partial result. |

## 7. Functional requirements

| ID | Requirement | Rules |
|----|-------------|-------|
| FR-1 | Validate a claim and return a decision for every line: approved amount, rejected amount and violations | BR-01…BR-08 |
| FR-2 | Return claim totals: claimed, approved and rejected in INR | BR-03, BR-04 |
| FR-3 | Return the approval route for the claim | BR-09 |
| FR-4 | Explain every violation with a rule id, severity and a plain-language message an employee can act on | All |
| FR-5 | Reject malformed input as a whole with a clear validation error | BR-10 |
| FR-6 | Make the decision deterministic: the same input always gives the same output (no clock or randomness inside the engine; the submission date is an input) | – |
| FR-7 | Keep policy values (limits, thresholds, windows, tiers) in one configuration object with defaults matching this BRD, so Finance can change them without code changes | All |

## 8. Acceptance criteria

| ID | Given / When / Then |
|----|---------------------|
| AC-1 | **Given** a meals line of ₹1,200 in a tier-1 city with a receipt, **when** validated, **then** ₹1,200 is approved with no violations. |
| AC-2 | **Given** a meals line of ₹1,800 in a tier-2 city with a receipt, **then** ₹1,000 is approved, ₹800 is rejected, and there is one `warn` violation for BR-03. |
| AC-3 | **Given** a travel line of ₹501 without a receipt, **then** the line is fully rejected with a BR-02 violation. A ₹500 line without a receipt is approved (boundary). |
| AC-4 | **Given** a lodging line of ₹10,000 for a G4 employee, **then** ₹9,000 is approved and ₹1,000 is rejected with a `warn` violation. For a G6 employee, the full ₹10,000 is approved. |
| AC-5 | **Given** a submission date of 2026-11-26, **then** a line dated 2026-09-27 (60 days earlier) is accepted, a line dated 2026-09-26 (61 days) is rejected as late, and a line dated 2026-11-27 is rejected as a future date. |
| AC-6 | **Given** two identical lines (same date, category, amount and merchant, with the merchant in different letter case), **then** the first is evaluated and the second is rejected as a BR-06 duplicate. |
| AC-7 | **Given** a client_entertainment line without notes, **then** it is rejected under BR-07. With notes naming the client, it is evaluated normally. |
| AC-8 | **Given** a USD 100 line with a rate of 83.456789, **then** it is evaluated as ₹8,345.68 (half-up). A EUR line with no EUR rate is rejected under BR-08. |
| AC-9 | **Given** approved totals of ₹25,000.00, ₹25,000.01, ₹1,00,000.00 and ₹1,00,000.01, **then** the routes are manager, finance controller, finance controller and CFO respectively. |
| AC-10 | **Given** a claim where every line is rejected, **then** the route is `no approval needed (fully rejected)`. |
| AC-11 | **Given** a claim with no lines, a missing employee id or an unknown category value in the header, **then** a validation error is raised and no partial result is returned. |
| AC-12 | **Given** a limit changed in the configuration (for example tier-1 meals at ₹2,000), **then** the engine applies the new value without code changes. |

## 9. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | Money is handled as exact decimals or integer minor units, never binary floating point. |
| NFR-2 | Validates a 200-line claim in under 100 ms on a developer laptop. |
| NFR-3 | No network calls, no file or database access, and no global mutable state (pure, embeddable logic). |
| NFR-4 | Automated tests cover every acceptance criterion and every boundary in section 6. |
| NFR-5 | Violation messages contain no personal data beyond what is in the claim. |

## 10. Sample input and output (illustrative)

```json
{
  "claim_id": "CLM-1001", "employee_id": "E-2231", "grade": "G4", "submitted_on": "2026-11-26",
  "rates": {"USD": 83.456789},
  "lines": [
    {"id": "L1", "date": "2026-11-20", "category": "meals", "amount": 1800, "currency": "INR",
     "city_tier": 2, "receipt": true, "merchant": "Cafe Uno"},
    {"id": "L2", "date": "2026-11-21", "category": "lodging", "amount": 10000, "currency": "INR",
     "city_tier": 1, "receipt": true, "merchant": "Hotel Grand"},
    {"id": "L3", "date": "2026-11-21", "category": "travel", "amount": 100, "currency": "USD",
     "receipt": true, "merchant": "Airline X"}
  ]
}
```

Expected outcome: L1 approved ₹1,000 (BR-03 warning); L2 approved ₹9,000 (BR-04 warning); L3 approved ₹8,345.68.
Total approved ₹18,345.68, so the route is **manager**.

## 11. Assumptions and constraints

- Exchange rates and the submission date are always supplied by the calling channel.
- City tier is supplied on meals and lodging lines; it is not derived from city names.
- The engine is a library or module embedded in each channel's backend. Choose the language that fits the target
  repository (for a new repository, any mainstream stack with a standard test framework).

## 12. Out-of-scope follow-ups (future phases)

Per-diem allowances, mileage claims, multi-employee meals, policy exceptions with pre-approval, and audit export.

---

### How to use this BRD to test Agentic SDLC

1. Open any repository in VS Code: an empty folder (greenfield) or an existing Java, .NET, Node, Python or Go
   service. The stack is detected automatically.
2. Right-click this file → **Agentic SDLC: Run on this file**, or from a terminal:
   `python orchestrator/agentic.py run --request-file BRD-expense-claim-policy-engine.md -w <repo> --approver deferred`
3. Approve the hires, watch the verify gate run your stack's tests, then review the release approval. The findings
   and verdicts should map back to AC-1 to AC-12.
4. Check the generated `docs/requirements.md` (in `.agentic-sdlc/projects/<id>/`) against section 8. Every AC should
   be traceable to a test.
