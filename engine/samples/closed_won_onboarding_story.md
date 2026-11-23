Request from the Head of Sales Operations (Salesforce Sales Cloud):

When a sales rep marks an Opportunity as Closed Won, the onboarding team has to be told by email today,
and customers wait days. Automate the handoff in Salesforce:

- Create an onboarding Case on the customer's Account (subject "Onboarding: <Account name>", origin "Sales Handoff").
- Priority High when the deal amount is 100,000 or more, otherwise Medium.
- Do not create a second onboarding case if the Account already has an open one; one case per Account even if
  several of its deals close at the same time.
- Mark the Account's customer status as "Active Customer".
- Only when a deal becomes Closed Won, not on every later edit. Closed Lost deals do nothing.
- Sales Ops bulk-closes up to 200 opportunities at a time with Data Loader, so it must work for bulk updates.
- Sales reps run on the Standard User profile; give them only the access the automation needs.
