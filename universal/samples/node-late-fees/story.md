Request from the Finance Systems lead (billing service, Node.js):

Customers who pay invoices late should be charged the late fee in our terms of service, calculated the same way
everywhere (portal, invoicing batch, collections). Please add a late-fee calculator to the billing service.

Terms of service:
- 5-day grace period after the due date: paying on day 5 after the due date is still free.
- After that: a flat fee of ₹250 plus 1.5% of the outstanding amount for every started 30-day period late
  (day 6 to day 30 counts as one period, day 31 starts the second).
- The total late fee can never exceed 10% of the original invoice amount.
- Partial payments: the 1.5% applies to what was still outstanding; the 10% cap uses the original invoice amount.
- Amounts are in paise (integers). Dates are calendar dates (YYYY-MM-DD).
- Bad input (negative or fractional amounts, outstanding above the invoice amount, invalid dates) must be rejected
  with a clear error, never silently charged.
