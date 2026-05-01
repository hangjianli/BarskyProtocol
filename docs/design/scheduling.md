# Scheduling

## Current Baseline

The system uses a Leitner-style fallback scheduler in early versions.

Bootstrap behavior:

- `pass` -> move up one box, capped at box 5
- `fail` -> reset to box 1
- `incomplete` -> keep the current box and reschedule soon
- admin reset of overdue cards -> move back to box 1 and make due now

Bootstrap intervals:

| Box | Interval |
| --- | --- |
| 1 | 1 day |
| 2 | 2 days |
| 3 | 4 days |
| 4 | 8 days |
| 5 | 16 days |

## Long-Term Direction

The long-term design is an adaptive scheduler with per-card-type policies.

Rules:

- `concept` and `code_exercise` cards should not share the same interval logic
- scheduling should consume review history and analytics signals
- analytics should inform scheduling, not replace it

## Adaptive Signals

### Concept Cards

Useful signals:

- `pass`, `fail`, `incomplete`
- review duration
- recent lapse history
- topic difficulty
- recall stability

### Code Exercises

Useful signals:

- `pass`, `fail`, `incomplete`
- retries
- failing test names
- review duration
- incomplete rate
- implementation stability

## Transparency Requirement

In v1, scheduling should be maximally explainable.

Every scheduling decision should show:

- scheduler policy
- previous interval
- new interval
- key inputs
- short reason summary

Administrative reset actions should also leave a clear reason on the card so the
user can tell that the change came from an overdue reset instead of a normal
review result.

Example:

- `Scheduler: Leitner fallback`
- `Previous box: 3`
- `New box: 1`
- `Next review: 1 day`
- `Reason: failed review reset the card to box 1.`

## Rollout Strategy

Phase A:

- fixed fallback scheduler for all cards

Phase B:

- adaptive scheduling for `concept` cards

Phase C:

- adaptive scheduling for `code_exercise` cards

Phase D:

- analytics-informed tuning while preserving explainability
