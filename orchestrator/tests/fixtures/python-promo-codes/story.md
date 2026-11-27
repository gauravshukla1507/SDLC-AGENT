Feature request from the e-commerce product owner:

Customers at checkout should be able to apply a promo code to get the advertised discount.

Business rules we already know:
- Two kinds of promo codes: percentage off (e.g. SAVE10 = 10% off) and fixed amount off (e.g. FLAT500 = 500 off).
- Some codes need a minimum order value (e.g. FLAT500 only on orders of 2,000 or more).
- Codes have an expiry date; a code is still valid on its expiry date.
- Codes are case-insensitive for customers ("save10" works).
- Only one promo code per order.
- The order total can never go below zero.
- Amounts are money in INR with 2 decimal places; round half-up.
- If a code can't be applied, the customer must see a clear reason (unknown, expired, minimum not met).
