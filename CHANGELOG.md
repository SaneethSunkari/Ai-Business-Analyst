# Changelog

This changelog tracks the product and engineering evolution of AI Data Middleware.

## Current

- Added portfolio documentation for roadmap, validation, and iteration history.
- Clarified the repository layout so reviewers know the source lives in `de-10-ai-data-middleware/`.
- Updated the project positioning around agent-compatible data access, SQL safety, and operational controls.

## v0.4 - Product And Control Plane

- Added a public product entry flow with landing page, login/signup, protected workspace, and profile/logout UX.
- Added Supabase-backed control-plane concepts for users, organizations, saved sources, schema snapshots, and query history.
- Added an operations status surface for runtime, onboarding, cost-control, and recent activity.

## v0.3 - Universal Connector Direction

- Expanded the connector catalog beyond relational databases toward warehouses, object stores, SaaS apps, NoSQL, and streams.
- Added object-store and file-backed query paths through temporary/virtual table patterns.
- Documented the connector expansion strategy in `de-10-ai-data-middleware/docs/UNIVERSAL_CONNECTOR_STRATEGY.md`.

## v0.2 - Agent-Compatible Database API

- Added schema introspection, saved connections, natural-language SQL generation, SQL execution, and query audit logging.
- Added OpenAI-style function-calling manifest support through `/tools/manifest` and `/tools/invoke`.
- Added a browser UI for asking questions, running SQL, viewing schema, reviewing agent tools, and checking history.

## v0.1 - Core Middleware Prototype

- Built the initial FastAPI middleware layer for PostgreSQL, MySQL, and SQLite.
- Added read-only SQL validation to block destructive keywords and unsafe statements.
- Added a Dockerized demo PostgreSQL database seeded with healthcare-style tables.
