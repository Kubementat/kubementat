"""`kmt uninstall`: every kubementat-owned object, taken off the cluster in one order.

The order is stated in `kubementat`, and the half that does the deleting is here so that the
command that needs it can read it without a class holding both halves open. A namespace delete
cannot reach cluster-scoped objects, a helm uninstall cannot reach a chart's `crds/`, and a list
of names written in this checkout cannot reach what the pinned release added -- so what belongs to
kubementat is asked of the cluster, and only the objects this checkout can name are taken by name.

The package marker exists for the reason `kubementat.automations.__init__` gives: no marker, no
installed package, `ImportError` on someone else's machine.
"""
