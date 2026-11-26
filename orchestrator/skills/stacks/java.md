Java conventions: Java 17+, Maven or Gradle standard layout (`src/main/java`, `src/test/java`), JUnit 5 with
AssertJ or JUnit assertions, constructor injection, immutable value objects (records), `BigDecimal` for money,
no checked exceptions leaking from service APIs. `mvn verify` / `gradle check` must pass.
