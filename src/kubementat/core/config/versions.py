"""What this build is, and what a stamped constraint says about it.

The version half of the tree's stamp, and nothing else. `ConfigurationStamp` owns the stamp and asks
these two questions of it, and it does not own the answers — the same comparison spelled twice is how a
tree ends up with a verdict that depends on which verb asked.

`product_version` asks the packaging metadata and never a literal, because `pyproject.toml` already has
the first owner of the version and a second owner is how a release drifts. Missing metadata answers
`None` rather than inventing a string, and an unknown version is exactly the case a warn-and-continue
rule must not read as "fine". `satisfies_version_constraint` answers `>=0.4,<2`-shaped clauses from the
operator table below, in about 30 lines, so `packaging` stays undeclared.

The two privates are that parser's arithmetic and nothing more: `_version_parts` truncates a component
at its first non-digit so a release candidate compares as its base version, and `_compare_versions`
pads both sides so `0.4` and `0.4.0` are one version and not two. Either raising on a shape it did not
expect would brick the gentle warning this module exists to emit.
"""

from importlib import metadata
from typing import Optional


_PRODUCT_DISTRIBUTION = "kubementat"
_CONSTRAINT_OPERATORS = (">=", "<=", "==", ">", "<")


def product_version() -> Optional[str]:
    '''What this build of kubementat is, asked of the packaging metadata and not of a literal.

    A literal here would be a second owner of the version, and `pyproject.toml:3` already has the
    first one. When the metadata is unavailable this answers `None` rather than inventing a version:
    The rule is warn-and-continue, and an unknown version is exactly the case that must not be
    silently treated as "fine" — it warns that it could not compare.
    '''
    try:
        return metadata.version(_PRODUCT_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None


def _version_parts(text: str) -> tuple:
    """A dotted numeric version as a tuple of ints, stopping a component at its first non-digit.

    Truncating instead of raising on `1.2.3rc1` is deliberate: a release candidate is not a different
    version from the point of view of a constraint that only ranges, and refusing to read a product
    version because it carries a suffix would brick the very warning that is supposed to be gentle.
    """
    parts = []
    for component in str(text).strip().split("."):
        digits = ""
        for char in component:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            raise ValueError(f"{text!r} is not a dotted numeric version")
        parts.append(int(digits))
    return tuple(parts)


def _compare_versions(one: tuple, two: tuple) -> int:
    """`0.4` and `0.4.0` are the same version, which is why both sides are padded before comparing."""
    width = max(len(one), len(two))
    left = tuple(one) + (0,) * (width - len(one))
    right = tuple(two) + (0,) * (width - len(two))
    return (left > right) - (left < right)


def satisfies_version_constraint(constraint: str, version: str) -> bool:
    '''Does `version` fall inside `constraint`, which is a comma-separated list of clauses?

    `>=0.4,<2` is the shape this answers, because it is the shape `constants` stamps and the shape
    `pyproject.toml` uses. It is about 30 lines, which is the honest cost of not adding
    `packaging` to the dependencies for two operators — `packaging` is importable here only as a
    transitive neighbour, and `test_the_declared_dependencies_are_what_the_tree_needs.py` refuses a
    dependency the manifest does not name. If this ever needs `!=`, a wildcard, or a prerelease
    comparison, that is the signal to declare the dependency and not to grow this parser.
    '''
    got = _version_parts(version)
    for clause in (part.strip() for part in str(constraint).split(",")):
        if not clause:
            continue
        operator = next((op for op in _CONSTRAINT_OPERATORS if clause.startswith(op)), None)
        if operator is None:
            raise ValueError(f"{clause!r} in {constraint!r} names no supported operator")
        order = _compare_versions(got, _version_parts(clause[len(operator):]))
        holds = {
            ">=": order >= 0,
            "<=": order <= 0,
            "==": order == 0,
            ">": order > 0,
            "<": order < 0,
        }[operator]
        if not holds:
            return False
    return True
