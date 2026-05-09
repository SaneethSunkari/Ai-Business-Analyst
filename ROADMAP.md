# Roadmap

AI Data Middleware is strongest when it is presented as a controlled bridge between AI agents and enterprise data, not just a text-to-SQL demo.

## Now

- Keep read-only SQL validation as a core safety feature.
- Keep the OpenAI-compatible tools manifest stable for agent integrations.
- Keep the local demo database and UI easy to run for reviewers.
- Keep project docs clear about what is implemented versus blueprint/roadmap.

## Next

- Add automated tests for auth, connection registration, schema introspection, SQL validation, tool invocation, and object-store scanning.
- Add a CI workflow for the backend test suite.
- Add examples for OpenAI, LangChain, and direct HTTP tool invocation.
- Add clearer onboarding screens for connecting a database safely.
- Add cost-control and routing examples around the TokenFirewall integration.

## Later

- Add tenant isolation tests for multi-organization usage.
- Add secrets storage guidance for production deployments.
- Add warehouse connectors for Snowflake, BigQuery, and Redshift.
- Add data-source permission scopes so agents can only query approved tables/views.
- Add observability dashboards for latency, query volume, token usage, blocked SQL, and connector health.

## Done Criteria For Production Readiness

- Every connector has tests for introspection, read-only querying, failure handling, and credential validation.
- SQL validation is backed by parser-based tests across supported dialects.
- Tenant isolation and audit logging are enforced server-side.
- Secrets are never stored in plain local files in production mode.
- Deployment docs include rollback, logging, monitoring, and incident-response notes.
