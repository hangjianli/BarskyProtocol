# BarskyProtocol

`BarskyProtocol` is a local CLI study system built around a 5-box Leitner schedule.
It is designed for anything you want to retain: technical concepts, vocabulary,
code patterns, quiz misses, definitions, or debugging heuristics.

## Study Model

Each card starts in box 1 and becomes due immediately. During review:

- Correct answer: move up one box, up to box 5
- Wrong answer: reset to box 1

Default review intervals:

- Box 1: 1 day
- Box 2: 2 days
- Box 3: 4 days
- Box 4: 8 days
- Box 5: 16 days

This keeps the system simple, predictable, and easy to tune.

## Quick Start

1. Initialize storage:

```bash
python3 cli.py init
```

2. Add a card:

```bash
python3 cli.py add \
  --topic python \
  --tags "asyncio,concurrency" \
  --prompt "What problem does a mutex solve?" \
  --answer "It prevents multiple threads or tasks from mutating shared state at the same time."
```

3. See what is due:

```bash
python3 cli.py due
```

4. Run a review session:

```bash
python3 cli.py review
```

## Commands

- `init`: create the SQLite database and schema
- `add`: create a new study card
- `due`: list cards due for review today
- `review`: run an interactive review session for due cards
- `list`: inspect recently created cards
- `stats`: show queue and review counts

## Good Card Design

- Keep cards atomic: one concept per card
- Prefer question/answer format over raw notes
- Capture why something matters, not just syntax
- Turn mistakes from quizzes or debugging sessions into cards quickly

Examples:

- "What does `git rebase` change compared to `git merge`?"
- "Why can a stale closure break a React event handler?"
- "When should I use `asyncio.gather()`?"

## Configuration

The CLI reads [config.toml](/Users/hangjianli/Projects/BarskyProtocol/config.toml) from the current
directory or one of its parents. You can override the path with
`BARSKY_CONFIG=/path/to/config.toml`.

Storage is local and uses SQLite by default.
