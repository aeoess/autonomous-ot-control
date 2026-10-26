"""Deployment-owned REMORA OT adapter (SDD-OT-001 §3, OT-003).

This package is the small, deployment-owned bridge installed into the *protected
REMORA execution image*. It registers the frozen v1 OT tool callables and, for
the single write tool, calls the twin's private actuator using the actuator
credential that is mounted only into that image.

It is NOT an authorization engine. It never mints a grant, lease or policy
decision, and never imports REMORA internals (``remora.enforcement`` etc.) as a
library. REMORA alone authorizes; this adapter only executes an already-governed,
already-bound call (INV-02, INV-03, INV-11). The public REMORA client surface is
``remora.sdk``; the server-side registration hook is a version-pinned deployment
integration (D-03), reconciled against the pinned commit in ``upstream/remora.lock.json``.
"""
