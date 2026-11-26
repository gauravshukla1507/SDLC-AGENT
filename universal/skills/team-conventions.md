Team conventions (company skill, versioned with the repo; applies to every stack):
- Name things by business domain: `<Domain>Service` for business logic, thin entry points (controllers, handlers,
  triggers, CLI) that only route to services, and `<Unit>Test` / `test_<unit>` / `<unit>.test` for tests.
- If the repository already has a framework for something (trigger handler, DI container, router, logger), extend it.
- Values the business may change (thresholds, fees, feature switches) go in configuration, not code constants,
  unless the design says otherwise. Every automation that reacts to data has a documented bypass for data migrations.
- Tests reference the acceptance-criterion id (AC-n) in their name, docstring or assertion message.
- Target 90% line coverage per changed unit; the platform or CI minimum is not the goal.
