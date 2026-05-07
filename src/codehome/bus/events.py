"""Canonical event registry for the unified bus.

Every event in the system is registered here with its transport class,
audit flag, and description.  This is the single source of truth for
event names -- no other module should call ``registry.register()``.

Old-name -> new-name mapping is documented inline so future readers can
trace back to the legacy SSE / JSONL names.
"""

from __future__ import annotations

from codehome.bus.registry import EventRegistry
from codehome.bus.types import Transport

D = Transport.DONE  # shorthand for readability


def build_registry() -> EventRegistry:
    """Build and return the populated event registry."""
    reg = EventRegistry()

    # ------------------------------------------------------------------
    # Branch lifecycle
    # ------------------------------------------------------------------
    # old: branch:created (SSE) / branch.start (JSONL)
    reg.register("branch.create.DONE", D, audit=True, description="Branch created")
    # old: branch:closed (SSE) / branch.end (JSONL)
    reg.register("branch.close.DONE", D, audit=True, description="Branch closed/finalized")
    # old: branch:renamed (SSE)
    reg.register("branch.rename.DONE", D, description="Branch renamed")
    # old: branch:changed (SSE)
    reg.register("branch.switch", D, description="Active branch changed")
    # old: branch.push (JSONL only)
    reg.register("branch.push", D, audit=True, description="Branch pushed to remote")

    # ------------------------------------------------------------------
    # Services (server-managed docker services)
    # ------------------------------------------------------------------
    # old: service:state
    reg.register("service.state", D, description="Service state transition")
    # old: service:deps
    reg.register("service.deps", D, description="Service dependency status")
    # old: service:log
    reg.register("service.log", D, description="Service container log line")
    # old: service:health
    reg.register("service.health", D, description="Service health check result")
    # was "metrics" (SSE)
    reg.register("service.metrics", D, description="Service metrics")

    # ------------------------------------------------------------------
    # Agent (Claude Code sessions managed by conductor)
    # ------------------------------------------------------------------
    # old: agent:state
    reg.register("agent.state", D, description="Agent state transition")
    # old: agent:output
    reg.register("agent.output", D, description="Agent text output")
    # old: agent:complete
    reg.register("agent.run.DONE", D, audit=True, description="Agent run completed")
    # old: agent:event
    reg.register("agent.event", D, description="Agent lifecycle event")
    # old: agent:question
    reg.register("agent.question", D, description="Agent asked a question")

    # ------------------------------------------------------------------
    # Conductor (orchestrates agent sessions)
    # ------------------------------------------------------------------
    # old: conductor:started
    reg.register("conductor.start.DONE", D, description="Conductor started")
    # old: conductor:stopped
    reg.register("conductor.stop", D, description="Conductor stopped (deliberate)")
    # new -- no old equivalent
    reg.register("conductor.crash", D, description="Conductor crashed")
    # old: conductor:message
    reg.register("conductor.message", D, description="Conductor message")
    # old: conductor:autonomy
    reg.register("conductor.autonomy", D, description="Conductor autonomy mode change")

    # ------------------------------------------------------------------
    # Rebase
    # ------------------------------------------------------------------
    # old: rebase:progress
    reg.register("rebase.progress", D, description="Rebase progress update")
    # old: rebase:complete + rebase:error (merged -- outcome in payload)
    reg.register("rebase.DONE", D, audit=True, description="Rebase completed (outcome in payload)")
    # old: rebase:conflict
    reg.register("rebase.conflict", D, description="Rebase hit conflicts")

    # ------------------------------------------------------------------
    # Tests & TDD
    # ------------------------------------------------------------------
    # old: tests:output
    reg.register("tests.output", D, description="Test runner output")
    # old: tests:result
    reg.register("tests.run.DONE", D, audit=True, description="Test run completed")
    # old: tdd:state
    reg.register("tdd.phase", D, description="TDD phase transition")
    # old: tdd:output
    reg.register("tdd.output", D, description="TDD runner output")

    # ------------------------------------------------------------------
    # Plan execution
    # ------------------------------------------------------------------
    # old: plan:state
    reg.register("plan.state", D, description="Plan execution state")

    # ------------------------------------------------------------------
    # Remote (git remote tracking)
    # ------------------------------------------------------------------
    # old: remote:fetch_complete
    reg.register("remote.fetch.DONE", D, description="Remote fetch completed")
    # old: remote:new_branch
    reg.register("remote.branch.create", D, description="New remote branch detected")
    # old: remote:deleted_branch  (ghost -- no consumer)
    reg.register("remote.branch.delete", D, description="Remote branch deleted")

    # ------------------------------------------------------------------
    # Terminal
    # ------------------------------------------------------------------
    # old: terminal:presence
    reg.register("terminal.presence", D, description="Terminal session presence")

    # ------------------------------------------------------------------
    # Design mode
    # ------------------------------------------------------------------
    # old: design:status
    reg.register("design.status", D, description="Design mode status")

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------
    # old: review:posted
    reg.register("review.post.DONE", D, audit=True, description="Review comments posted")
    # old: review:resolved
    reg.register("review.resolve.DONE", D, description="Review threads resolved")

    # ------------------------------------------------------------------
    # Linear integration
    # ------------------------------------------------------------------
    # was "linear.outgoing" (JSONL)
    reg.register("linear.mutation", D, audit=True, description="Linear API mutation")
    # was "linear.incoming" (JSONL)
    reg.register("linear.sync", D, audit=True, description="Linear sync from API")

    # ------------------------------------------------------------------
    # Telemac (remote Mac / iOS pipeline)
    # ------------------------------------------------------------------
    # old: telemac:state
    reg.register("telemac.state", D, description="Telemac machine state")

    # ------------------------------------------------------------------
    # Generic operations
    # ------------------------------------------------------------------
    # old: operation:progress
    reg.register("operation.progress", D, description="Generic operation progress")
    # old: operation:output
    reg.register("operation.output", D, description="Generic operation output")

    # ------------------------------------------------------------------
    # Reveng (reverse-engineering pipeline)
    # ------------------------------------------------------------------
    reg.register("reveng.extract.DONE", D, audit=True, description="RevEng extraction completed")
    reg.register("reveng.graph.DONE", D, audit=True, description="RevEng graph construction completed")
    reg.register("reveng.classify.DONE", D, audit=True, description="RevEng classification completed")
    reg.register("reveng.emit.DONE", D, audit=True, description="RevEng YAML emission completed")
    reg.register("reveng.validate.DONE", D, audit=True, description="RevEng validation completed")
    reg.register("reveng.verify.DONE", D, audit=True, description="RevEng DOM verification completed")
    reg.register("reveng.status", D, description="RevEng status update")

    # ------------------------------------------------------------------
    # Figma2SDUI (design-to-code pipeline)
    # ------------------------------------------------------------------
    reg.register("figma2sdui.graph.updated", D, description="SDUI graph data changed (conversion ran)")

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------
    reg.register("feature.changed", D, description="Feature flags updated")

    # ------------------------------------------------------------------
    # Ghosts (registered but never fired in current codebase)
    # ------------------------------------------------------------------
    # old: pipeline:update
    reg.register("pipeline.state", D, description="Pipeline status")
    # was "ports" (SSE)
    reg.register("ports.state", D, description="Port allocation")

    return reg


# Module-level singleton -- import this for runtime use.
registry = build_registry()
