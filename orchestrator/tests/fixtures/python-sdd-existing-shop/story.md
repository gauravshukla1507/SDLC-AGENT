# Promo codes at checkout

Change request for the existing shop.

Customers must be able to apply one promo code to their cart at checkout.

- Percentage codes (e.g. SAVE10) and fixed-amount codes (e.g. FLAT500).
- A code can have a minimum order value; below it the code is rejected with "minimum not met".
- Codes are valid up to and including their expiry date; after it they are rejected with "expired".
- Codes are matched case-insensitively; surrounding spaces are ignored.
- Only one code per order. The total can never go below zero.
- All amounts in INR, 2 decimals, rounded half-up (use the shop's existing money rules).
- Unknown codes are rejected with "unknown code".
