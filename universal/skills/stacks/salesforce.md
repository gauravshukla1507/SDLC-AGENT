Salesforce engineering standards for this team (non-negotiable):
- SFDX source format under force-app/main/default/ (classes/, triggers/, objects/<Object>/fields/, permissionsets/).
- Every .cls and .trigger has a matching -meta.xml with apiVersion 62.0 and status Active.
- Prefer declarative (Flow, validation rules) when it fully meets the need; use Apex when bulk logic,
  de-duplication or testability require it, and say why.
- One trigger per object, no logic in triggers: delegate to a handler class.
- Bulkify everything: no SOQL or DML inside loops; design for 200-record trigger batches and data loads.
- Security: classes declared `with sharing`; SOQL `WITH USER_MODE`; DML `as user`; no hardcoded Ids;
  grant access with a permission set, never by editing profiles.
- Tests: @IsTest private classes, @TestSetup data, no SeeAllData, Assert class with messages,
  positive, negative, bulk (200 records) and run-as-user cases; target >= 90% coverage (platform minimum 75%).
