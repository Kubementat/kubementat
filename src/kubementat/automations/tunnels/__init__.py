"""The port-forwards this product offers, and nothing else.

Three commands, one primitive (`infrastructure.kubernetes.tunnel.open_tunnel`) and one namespace
answer each. A tunnel is a read that lasts, so there is nothing to decide and nothing to
remember: the module is thin because the decision belongs to the component's configuration.

The package marker exists for the reason `kubementat.automations.__init__` gives: no marker, no
installed package, `ImportError` on someone else's machine.
"""
