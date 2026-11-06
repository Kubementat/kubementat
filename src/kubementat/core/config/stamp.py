"""What a prepared tree's stamp says, and what this build says about it.

The read side and nothing else. `config init` writes the two keys into `<root>/kubementat.yaml`; this
module asks them. Three names and one window, because two verbs ask the same two questions of the same
two keys: `check_configuration_stamp`, which every cluster command runs before it resolves anything,
and `config validate`, which gets to be strict. Two spellings of one comparison is how a tree ends up
with a verdict that depends on which verb was asked, so `ConfigurationStamp` owns the comparison and
both verbs read it off the same object.

`read_configuration_stamp` is the only reader of the file, and a missing stamp is a refusal rather
than a default. `check_configuration_stamp` splits the severity: a version string that drifted past
the range warns and continues, a schema two or more steps behind raises. It hands the warnings back
instead of printing them, because "warn once" is a property of how many times the command ran.

What this module is not. It holds no `Config` and reads no layered configuration, and it answers no
version question of its own — `running_version` and the constraint check are asked of `versions`,
whose `product_version` is patched by name in tests and so has to remain the module the stamp reads
through. The window is the constant below and nowhere else.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click
import yaml

from kubementat.core import constants
from kubementat.core.config import versions
from kubementat.core.config.reads import read_decrypted


# The read side of the tree's version stamp, and the reason it had to be written is that the write side has existed
# since `config init` began stamping. Two keys landed in every prepared tree and nothing read them,
# which is worse than having no stamp at all: a person skimming this tree finds a version gate and
# assumes it gates. From here the stamp is asked before the tree is trusted.
SCHEMA_VERSION_WINDOW = 1


def read_configuration_stamp(root: Path) -> tuple:
    '''The two keys of `<root>/kubementat.yaml`, or a refusal that names the verb that writes them.

    A missing stamp is a refusal and not a default. No backwards compatibility lives in this epic,
    and the alternative — assume `schema_version: 1` when the file is absent — rescues a tree that
    was never stamped, which is the same mistake as a library function silently bumping it.
    '''
    path = Path(root) / constants.CONFIG_MANIFEST_FILENAME
    if not path.is_file():
        raise click.ClickException(
            f"{path} is absent, so {root} does not say which product it was prepared for. "
            "kmt config init writes this file; a directory that predates the stamp has never been "
            "stamped and cannot be assumed to agree with this build.")
    try:
        stamp = yaml.safe_load(read_decrypted(path))
    except yaml.YAMLError as error:
        raise click.ClickException(f"{path} is not readable YAML: {error}")
    if not isinstance(stamp, dict):
        raise click.ClickException(
            f"{path} must be a mapping of the two keys this product stamps, and reads as "
            f"{type(stamp).__name__}")
    constraint, schema = stamp.get("kubementat"), stamp.get("schema_version")
    if constraint is None or schema is None:
        raise click.ClickException(
            f"{path} declares kubementat={constraint!r} and schema_version={schema!r}; both keys are "
            "required, one because it is the constraint this tree was validated against and one "
            "because it is the only thing that says what its answers mean.")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise click.ClickException(f"{path} declares schema_version={schema!r}; it must be an integer")
    return str(constraint), int(schema)


@dataclass(frozen=True)
class ConfigurationStamp:
    '''A tree's stamp, and what this build says about it.

    One owner for the window. Two verbs ask the same two questions of the same two keys:
    `check_configuration_stamp`, which every cluster command runs, and `config validate`, which gets
    to refuse (it is the only strict home for version drift). Two spellings of one comparison is how a
    tree ends up with a verdict that depends on which verb was asked, which is the state that has
    to be refused outright.

    The conditions are properties and the sentences stay with the verbs. A fact that carried its own
    wording would be prose in three files again, and what a warning rule wants is the same fact everywhere and
    not the same paragraph.

    Two distinctions this object is the place for: `schema_is_too_old`, the refusal, is not the
    same fact as `schema_is_behind_window` (one step behind, which is inside the window and needs no
    remedy), and a version this build cannot determine is not the same fact as a constraint this build
    falls outside. Only the second is the tree's mistake.
    '''

    root: Path
    constraint: str
    schema: int

    @classmethod
    def read(cls, root) -> "ConfigurationStamp":
        """The stamp of `root`, read through the one function that refuses to guess it."""
        constraint, schema = read_configuration_stamp(root)
        return cls(root=Path(root), constraint=constraint, schema=schema)

    @property
    def current(self) -> int:
        """What this build is. The same number `kmt --version` answers, from one source."""
        return constants.CONFIG_SCHEMA_VERSION

    @property
    def window(self) -> int:
        """How many steps back this build reads. One, and no fixture lane makes a wider window
        honest."""
        return SCHEMA_VERSION_WINDOW

    @property
    def oldest_accepted(self) -> int:
        return self.current - self.window

    @property
    def schema_is_too_old(self) -> bool:
        return self.schema < self.oldest_accepted

    @property
    def schema_is_behind_window(self) -> bool:
        return self.oldest_accepted <= self.schema < self.current

    @property
    def running_version(self) -> Optional[str]:
        return versions.product_version()

    @property
    def version_is_determinable(self) -> bool:
        return self.running_version is not None

    @property
    def version_is_outside_constraint(self) -> bool:
        """The tree's constraint against this build. An undeterminable version answers `False`, and
        the reader has to have asked `version_is_determinable` to know why nothing was said.

        A clause that names no operator raises out of `satisfies_version_constraint`. A verb that
        collects faults rather than raising should turn that into a finding; a verb that warns can
        let it pass, which is what `check_configuration_stamp` has always done with it.
        """
        running = self.running_version
        if running is None:
            return False
        return not versions.satisfies_version_constraint(self.constraint, running)

    @property
    def version_gap_sentence(self) -> str:
        """The version half, spelled once: both versions, and the fact that it warns."""
        return (f"this kubementat is {self.running_version} and {self.root} declares it was validated "
                f"against {self.constraint!r}. Continuing because a version string is not a migration.")


def check_configuration_stamp(root: Path) -> list:
    '''Refuse what is too old, warn about what is merely outside the range, hand back the warnings.

    Two checks with two different severities, and the split is the requirement and not a taste:

    * **The version string warns and continues.** A running cluster must not be bricked because a
      product version drifted past a range. `config validate` is the verb that gets to be strict
      about it.
    * **The schema version refuses.** Two or more steps behind and every answer this tree gives that
      is not named in its own layer is coming from a built-in default, so the cluster is configured
      by kubementat's assumptions and not by the operator's.

    It returns warnings instead of printing them, because "warn once" is a property of how many
    times the command ran and not of this function. A module-level memo would make the first test in
    a process the only one that can see the warning, and a test that asserts a warning only passes
    by accident when it runs first.

    The refusal must be reached before anything touches the cluster, which is why the only
    filesystem work here is the stamp itself.
    '''
    constraint, schema = read_configuration_stamp(root)
    stamp = ConfigurationStamp(root=root, constraint=constraint, schema=schema)
    if stamp.schema_is_too_old:
        raise click.ClickException(
            f"{root} declares schema_version {schema} and this kubementat is at "
            f"{stamp.current}, which is more than {stamp.window} step(s) "
            "behind. Answers this tree does not name for itself would come from built-in defaults, "
            "which configures the cluster by kubementat's assumptions and not by yours. "
            f"Raise schema_version in {root / 'kubementat.yaml'} to close the gap; that bump is a "
            "decision, and no command will make it for you.")

    warnings = []
    if not stamp.version_is_determinable:
        warnings.append(
            f"warning: kubementat could not determine its own version, so it cannot check the "
            f"{constraint!r} this tree declares. Continuing; run kmt --version to see what this is.")
    elif stamp.version_is_outside_constraint:
        warnings.append(f"warning: {stamp.version_gap_sentence}")
    return warnings
