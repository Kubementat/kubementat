"""The routing automations: what Traefik is on the cluster, and what to do about it.

Two modules, and the dependency between them runs one way on purpose. `traefik_presence` reads
the cluster and answers; `routing` decides and acts. A module that reads must not be able to
install, so `traefik_presence` imports nothing from this package's other half.

The package marker exists for the reason `kubementat.automations.__init__` gives: no marker, no
installed package, `ImportError` on someone else's machine.
"""
