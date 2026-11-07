"""Team accounts: a ServiceAccount, the Roles that say what it may do, and its kubeconfig.

The slice is thin on purpose. The verbs, the Rules and the binding names are `infrastructure.user_management`'s
answer, the writes are `infrastructure.kubernetes`'s, and what is left here is the order: name the
cluster and check the verbs, name the namespaces, create, bind, read back, print.

The package marker exists for the reason `kubementat.automations.__init__` gives: no marker, no
installed package, `ImportError` on someone else's machine.
"""
