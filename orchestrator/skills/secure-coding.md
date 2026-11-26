Secure-coding checklist (company skill):
- Enforce access in the data layer: user mode / with sharing / FLS checks, or the stack's equivalent.
- No dynamic queries built from user input; bind variables only.
- No secrets, tokens or record Ids in code. Use named credentials or a secret manager.
- Log security-relevant skips and denials so they can be audited; never fail silently.
- Rate-limit and cap the length of every externally supplied input at the API boundary.
- Report each issue with a severity and the exact file and line.
