# Analytics and Recommendations

## Purpose

The analytics layer explains why retention is failing. The scheduler decides
when to review; analytics explains what to change.

## Failure Signals

Track signals such as:

- repeated resets on the same card
- repeated failures by topic or tag
- repeated incompletes
- failing test names for exercises
- validator summaries
- cards stuck in low boxes

## Pattern Classes

Key pattern classes:

- unstable concept recall
- unstable implementation recall
- edge-case blindness
- oversized exercise scope
- topic overload
- repeated confusion between related ideas

## Recommendation Pipeline

Recommendation generation should follow two steps.

### 1. Deterministic Aggregation

- compute lapse-heavy cards
- compute weak topics and tags
- cluster recurring failing tests
- detect overloaded queues
- detect exercises with chronic resets

### 2. Recommendation Rendering

- convert findings into direct actions
- optionally use an LLM to phrase the output
- always tie each recommendation to explicit evidence

Good recommendation:

- “You failed 4 `asyncio` cards on cancellation. Add 2 smaller concept cards and pause new `asyncio` cards for 3 days.”

Bad recommendation:

- “Keep practicing async programming.”

## UI Surfaces

### `/patterns`

Show evidence such as:

- weak topics
- high-lapse cards
- repeated incompletes

### `/recommendations`

Show concrete next steps based on stored evidence.

Examples:

- split an oversized exercise
- add concept cards for a recurring failure theme
- add edge-case drills for repeated failing tests
- pause new cards in an overloaded topic
