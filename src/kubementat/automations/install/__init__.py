"""The install slice: the checks `kmt install` makes before it writes, and what it writes.

Four modules and a rule that holds them in order. `dependencies` and `kubementat` answer whether
this run may start at all, `pipelines` puts the Tasks and Pipelines on the cluster, `secrets` and
`registry` write what a pipeline needs to clone and to pull. Each of them is a set of module
functions taking a `Context`, the shape `automations.routing` proved out: nothing here builds
state that outlives one call, so no two callers can hold two answers to "which cluster".

The package marker exists for the reason `kubementat.automations.__init__` gives: no marker, no
installed package, `ImportError` on someone else's machine.
"""
