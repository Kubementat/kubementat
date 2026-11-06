"""Arithmetic on the platform's units: a Kubernetes quantity in, a number out.

Two of the three names here convert a quantity string the way the API server reads one, and the
third says what a kind is called in an API path. All three are pure: no cluster, no filesystem, no
subprocess, no configuration. They came out of the module that held the kubernetes calls because a
decision about *what a number means* does not belong in the module that is left holding them, and
because the module that asks the question is not always the one that ends up answering it —
`src/cli/commands/kubernetes.py` reads `parse_memory` without ever building a `KubernetesUtils`.

The rule this file keeps is the one it was written under: it imports the standard library and
`kubementat.errors` and nothing above it. No kubernetes module, no `automations`, no
`infrastructure`, so nothing that reaches up can reach down through it, and no cycle can be made
here by construction.
"""


def parse_cpu(value) -> float:
    """A Kubernetes quantity of cpu, in cores. "500m" -> 0.5, "2" -> 2.0, nanocores too."""
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        if text.endswith("n"):
            return float(text[:-1]) / 1_000_000_000.0
        return float(text)
    except ValueError as error:
        raise ValueError(f"Not a cpu quantity: {value!r}") from error


def parse_memory(value) -> float:
    """A Kubernetes quantity of memory, in bytes.

    Kubernetes writes the binary suffixes (Ki, Mi, Gi, ...) for most memory fields; the
    decimal ones (K, M, G, ...) are legal and show up from custom quantities, so both are
    read. A bare number is bytes.
    """
    text = str(value).strip()
    if not text:
        return 0.0
    suffixes = (
        ("Ei", 1024 ** 6), ("Pi", 1024 ** 5), ("Ti", 1024 ** 4), ("Gi", 1024 ** 3),
        ("Mi", 1024 ** 2), ("Ki", 1024),
        ("E", 1000 ** 6), ("P", 1000 ** 5), ("T", 1000 ** 4), ("G", 1000 ** 3),
        ("M", 1000 ** 2), ("k", 1000),
    )
    try:
        for suffix, factor in suffixes:
            if text.endswith(suffix):
                return float(text[:-len(suffix)]) * factor
        return float(text)
    except ValueError as error:
        raise ValueError(f"Not a memory quantity: {value!r}") from error


def plural_for_kind(kind: str) -> str:
    """The resource name a kind is addressed by in the API path.

    `f"{kind.lower()}s"` was the whole rule, and it broke on the one kind anything here applies
    that is not a Tekton resource: an `Ingress` lives under `ingresses`, so the naive plural asked
    for `ingresss` and the answer was a 404 that named neither the kind nor the path. Kubernetes
    itself pluralises by rule, and the two rules that cover every kind kubementat sends are the
    ones below -- which is why the answer is derived here and not written out per kind.
    """
    lowered = (kind or "").strip().lower()
    if lowered.endswith(("s", "x", "ch", "sh")):
        return lowered + "es"
    return lowered + "s"
