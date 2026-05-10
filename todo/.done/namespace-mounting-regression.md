# Namespace Mounting Regression (Phase 4 Declarative CLI)

Status: Urgent (blocks all super repo commits)
Introduced by: Phase 4 commit `d9c060d` (declarative CLI migration)

## Problem

The phase 4 refactoring removed `load_all_plugins()` from CLI startup, replacing it with `discover_plugins()` (TOML-only) + `build_commands()` (LazyHandler wrappers). This made CLI startup fast but broke two things:

### 1. Namespace mounting is gone

`_mount_plugin_namespaces()` in `plugins/loader.py` runs inside `load_all_plugins()`, which is no longer called during CLI invocation. 11 plugins declare namespaces (e.g., `plugins/core/` -> `codehome.core`). Any handler module with a module-level cross-namespace import crashes:

```
$ v check gate
ModuleNotFoundError: No module named 'codehome.core'
```

The crash happens in `LazyHandler._import()` (`cli_builder.py`), which does raw `importlib.util.spec_from_file_location` + `exec_module` with no namespace mounting.

Cross-namespace imports are widespread:
- `codehome.core.*` imported by: check_cmd, lisa, incantino, publisher, inspect
- `codehome.telemac.*` imported by: publisher
- `codehome.inspect.*` imported by: inspect submodules

### 2. Check registration is gone

`load_all_plugins()` also loaded each plugin's `checks.py` and registered check functions into `CheckRegistry`. Without it, `v check gate` finds zero checks. `CheckRegistry` is only populated via `load_all_plugins()`, which now only runs from `serve/server.py` (server startup).

## Impact

- `v check gate` crashes (namespace) and would be empty even if it didn't (registration)
- `v check precommit` crashes (same reason)
- All super repo commits are blocked (precommit hook runs `codehome check precommit`)
- `v check` subcommands are non-functional

## Root Cause

The declarative CLI migration removed Python imports from CLI startup but did not replace the side effects those imports provided. `load_all_plugins()` did three things:
1. Loaded plugin manifests (replaced by `discover_plugins()`)
2. Mounted plugin namespaces as `codehome.*` subpackages (NOT replaced)
3. Loaded `checks.py` and registered checks into `CheckRegistry` (NOT replaced)

## Proposed Fix

### Option A: Mount namespaces in LazyHandler._import()

Before `spec.loader.exec_module(mod)` in `LazyHandler._import()`, call `_mount_plugin_namespaces()` (once, on first handler import). This is lazy -- namespaces only mount when a command is actually invoked, not at CLI startup.

For check registration: `cmd_check` handler calls `load_all_plugins()` (or a lighter `_register_checks_from_plugins()`) at the top of its handler function.

### Option B: Add a namespace mounting step to CLI startup

After `discover_plugins()`, call `_mount_plugin_namespaces()` using the discovered manifests. This runs on every CLI invocation but is cheap (no Python imports, just `sys.modules` manipulation). Check registration stays lazy (loaded by cmd_check).

### Option C: Call load_all_plugins() lazily on first command invocation

`LazyHandler.__call__()` calls `load_all_plugins()` before importing the handler module. This is the heaviest option but preserves all existing behavior. Should be done once (cached).

## Recommendation

Option B is the cleanest. Namespace mounting is cheap (no file I/O beyond what `discover_plugins()` already did) and eliminates the class of errors where any handler import fails due to missing namespaces. Check registration can be lazy (option A style) since only `v check` commands need it.

## Affected Files

- `src/codehome/cli.py` -- add namespace mounting after discover_plugins
- `src/codehome/plugins/cli_builder.py` -- optionally add namespace mounting before exec_module
- `src/codehome/plugins/loader.py` -- possibly extract `_mount_plugin_namespaces` to be callable independently of `load_all_plugins`
