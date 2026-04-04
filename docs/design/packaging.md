# Local Packaging

## Goal

Distribute BarskyProtocol as an installable local app so other people can use it
without cloning the repo or managing a Python environment manually.

The first packaging target should preserve the current product model:

- local-first
- browser-based UI on `localhost`
- local SQLite database
- local card assets and coding workspaces
- user-controlled grading auth

## User Experience

Target flow:

1. Download and install BarskyProtocol.
2. Launch the app from the OS launcher.
3. The app creates local data directories automatically on first run.
4. The app starts the local server and opens the browser.
5. The user completes a short first-run setup.
6. The user lands on the dashboard and starts importing or creating cards.

The user should not need to:

- clone the repository
- run `python cli.py init`
- create a virtual environment
- know where runtime data is stored

## Packaging Strategy

### Phase 1: Installable Local App

Ship BarskyProtocol as an installable Python application with a launcher such
as `barskyprotocol`.

Behavior:

- initialize storage automatically if it does not exist
- start the local web server
- choose a free local port if the default is occupied
- open the browser automatically
- show a first-run setup screen when config is incomplete

This phase keeps the current browser-based UI and avoids the extra complexity
of a native shell.

### Phase 2: Native Bundles

After Phase 1 is stable, build standalone bundles or installers:

- macOS: `.app` / `.dmg`
- Windows: installer or packaged `.exe`
- Linux: AppImage or equivalent

This phase should keep the same local data model and startup flow.

### Phase 3: Optional Desktop Shell

Only after packaging is stable should we consider wrapping the app in a native
window. The shell should be a presentation layer, not a product redesign.

## Runtime Data Layout

Installed app mode should not store runtime state relative to the repo root.

Recommended app-data roots:

- macOS: `~/Library/Application Support/BarskyProtocol`
- Linux: `~/.local/share/BarskyProtocol`
- Windows: `%AppData%/BarskyProtocol`

Recommended contents:

- `config.toml`
- `study.db`
- `cards/`
- `workspaces/`
- `imports/`
- `logs/`

The existing repo-relative layout should remain available in development mode.

## Startup Flow

On launch:

1. Resolve the app-data directory.
2. Load or create config.
3. Create storage directories if missing.
4. Run storage initialization and migrations.
5. Start the web server on a free local port.
6. Open the browser to the local app.
7. Show first-run setup if required; otherwise show the dashboard.

## First-Run Setup

The first-run screen should collect:

- data directory
- default review mode
- grading mode
- auth diagnostics
- optional import of existing repo-local study data

This setup should feel like product onboarding, not developer bootstrap.

## Implementation Requirements

### Config and Paths

- separate `dev mode` and `installed app mode`
- derive data paths from an app-data root in installed mode
- keep packaged templates and static files loadable without relying on repo paths

### Launching

- add a launcher entrypoint for the installed app
- start and stop the local server cleanly
- open the browser automatically on successful startup

### Reliability

- support config and schema migrations
- store logs in the app-data root
- avoid terminal-only failure modes for common startup issues

## Tooling Recommendation

Recommended rollout:

1. package as an installable Python app with a console entrypoint
2. validate the launcher and first-run experience
3. add standalone bundles with PyInstaller or a similar packager

This path is lower-risk than introducing a desktop shell immediately.

## Non-Goals

This packaging plan does not imply:

- hosted multi-user SaaS
- cloud-synced storage
- notebook-native runtime artifacts
- an in-browser IDE for coding reviews
- moving local workspaces to remote execution

## Open Questions

- whether to support importing an existing repo-local `.barsky/` directory on first run
- whether the default browser launch should be optional in settings
- which platform to target first for polished native packaging
- how far auth diagnostics should go before they become setup noise
