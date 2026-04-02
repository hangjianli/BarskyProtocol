# BarskyProtocol Design

This file is now the design index. Detailed component docs live under
[`docs/design/`](./docs/design/).

## Purpose

BarskyProtocol is a local-first study system for:

- concept recall
- code reimplementation exercises
- review scheduling
- failure analysis
- recommendation generation

The product is built around spaced repetition, but specialized for technical
learning and coding practice.

## Reading Order

1. [Overview](./docs/design/overview.md)
2. [Review Flows](./docs/design/workflows.md)
3. [Source Import](./docs/design/notebook-import.md)
4. [Data Model and Storage](./docs/design/data-model.md)
5. [Scheduling](./docs/design/scheduling.md)
6. [Analytics and Recommendations](./docs/design/analytics.md)
7. [Architecture and Roadmap](./docs/design/architecture.md)

## Quick Map

- Product scope, goals, and UI principles:
  [overview.md](./docs/design/overview.md)
- Concept review, exercise review, validation, and workspace lifecycle:
  [workflows.md](./docs/design/workflows.md)
- Source import, split modes, regeneration, and metadata enrichment:
  [notebook-import.md](./docs/design/notebook-import.md)
- SQLite schema, provenance, and filesystem layout:
  [data-model.md](./docs/design/data-model.md)
- Leitner fallback, adaptive scheduler direction, and transparency rules:
  [scheduling.md](./docs/design/scheduling.md)
- Failure patterns, deterministic recommendations, and evidence rules:
  [analytics.md](./docs/design/analytics.md)
- Module layout, routes, testing plan, and implementation phases:
  [architecture.md](./docs/design/architecture.md)

## Current Principles

- Keep the system local-first and browser-based on `localhost`.
- Preserve a clear line between study artifacts and source provenance.
- Prefer deterministic behavior over opaque automation in v1.
- Make scheduler and recommendation logic explainable.
- Treat the source importer as a review-first pipeline, not a one-click card generator.
