"""`kmt config` -- the configuration directory, as a thing you create and not as a thing you guess.

The group exists because `config` is where the commands that *read and write an existing tree*
live: `config set`, `config unset`, `config show`, `config validate`. The group and its first
command landed together, so that the next of those four arrives on a surface that already has a shape
rather than as a fifth flat name someone has to guess a home for.

Seven verbs are on it, and the table of what is *not* on it is the rest of the answer. kubementat
reads a configuration tree and answers one key at a time; it does not rewrite an operator's tree, so
there is no verb that applies a rename table and no verb that reports what a version bump would cost.
Both were deleted on 2026-11-05, and their absence is part of the surface rather than a gap in it.

What is deliberately **not** on it, because a surface is defined by its absences as much as by its
rows:

* no `config unlock` -- kubementat never unlocks a configuration it is not currently writing
  (kubementat never unlocks); a verb that opened a vault to answer a question would be a second, unaudited path to
  the secrets that `static.encrypted.json` is encrypted for.
* no `config secrets` -- `config validate` is what answers "are these secrets fine", and it does it
  by reading the tree. A `secrets` verb would be a second opinion about the same file, which is how
  two answers get written and only one is read.
* no `config generate` -- `kmt tekton-generate-*` are those verbs, and they already name the kind of
  artifact they emit. A `config generate` would say only that something is emitted.

`init` is on the group for this reason: creating a tree is the one configuration act that
cannot be done by editing a file, because before the first byte there is no file. `add-environment`
and `add-team` are on it for the complementary reason: growing a tree is the one configuration act
that *can* be done by editing a file and should not be, because a hand-made layer is a layer whose
directories half of the readers open and half of them do not.

The verbs split by what they create, and the split is the whole surface argument: `init` owns the
manifest, the first environment, and the git half of the tree — the repository, git-crypt live in it,
and the deployer keypair that goes with both — while `add-environment` owns every environment after
that one and `add-team` owns a team under exactly one of them. The split is why `init` takes flags and
the layer verbs do not: preparing a directory asks questions that the layer verbs are never asked,
whose answers belong to the operator and not to the tree. Neither of the two layer verbs takes a flag
or writes an answer, because answering a key is a different act from laying down a place for answers:
`set` and `unset` are those two, and they are the first verbs on this surface that read a file before
they write one. What they must not do is answer a question nobody asked them, which is why neither
carries a `--yes` or a prompt. `set` carries `--generate`, and that flag is the whole answer on
generation: it was decided as *an option on one answer* rather than as a verb or a class of key, so the one
place a random value may be invented is the verb that already writes one answer to one key, and the
refusal that comes attached to it is what keeps a layer-creating verb from generating anything at all.
`unset` takes no `--encrypted`, because clearing one file of a pair is a change the reader never sees.
"""

import json
from pathlib import Path

import click

from kubementat import config_answers, config_bootstrap, config_init, config_keys, config_validate


@click.group(name='config',
             help='Create and edit a kubementat configuration directory. init lays a tree down; '
                  'add-environment and add-team add one layer to a tree that already exists and write '
                  'no answers, because config set is the verb that answers one key and config unset '
                  'is the verb that takes one away. What is not here is deliberate: no config unlock '
                  '(kubementat never unlocks), no config secrets (config validate answers '
                  'that), no config generate (kmt tekton-generate-* is that verb).')
def config():
    pass


@config.command(name='init',
                help='Prepare a configuration directory under <dir>, for one environment: the '
                     'manifest and the static layers, the deployer keypair, and git-crypt made live '
                     'in that directory -- .gitattributes declared before the first encrypted file, '
                     'the collaborator registered, the symmetric key exported at 0600, and one '
                     'commit of kubementat\'s own. It writes answers and no product shape, and it is '
                     'idempotent: a second run writes no byte, commits nothing, and says which step '
                     'it skipped. A fresh tree addresses no cluster until someone names one.')
# `directory` is a positional and not a flag because it is the thing the verb is *about*: every file
# written lands under it. No `--config-dir`, and no `KUBEMENTAT_PLATFORM_CONFIG_DIR` behind it --
# there is no root to point at here, and a verb that fell back to the variable would be a
# configuration directory located by proximity, wearing a flag.
@click.argument('directory')
@click.option('--environment', default=None,
              help='The environment whose layer is laid down. Required; there is no environment '
                   'that means all of them.')
@click.option('--team', default=None,
              help='Also create this team\'s two empty files. Needs --environment: a team is a '
                   'layer under one environment.')
# `multiple=True` and the `KEY=VALUE` metavar, because an answer is a key the operator named and not
# a flag that was invented. The value is typed (JSON) rather than `str()`-ed, so `--set
# GRAFANA_ENABLED=false` is a boolean and not the four-letter word.
@click.option('--set', 'set_answers', metavar='KEY=VALUE', multiple=True,
              help='Answer one key into the environment static file. The value is JSON when it '
                   'parses and a string otherwise, so false is a boolean and example.test is not.')
@click.option('--encrypted-set', 'encrypted_answers', metavar='KEY=VALUE', multiple=True,
              help='Answer one key into the environment encrypted static file, with the same '
                   'typing as --set. What lands there is encrypted by the git-crypt filter this '
                   'verb declares before it writes it.')
# The two ways a deployer GPG key arrives, spelled by the flags `config_keys` refuses with. No third
# spelling exists: deriving the identity from an environment variable is what `initialize` did, and
# the library leaves that answer to the caller.
@click.option('--gpg-key', 'gpg_key', metavar='FILE', default=None,
              help='Import this gpg secret key file as the deployer key instead of generating one. '
                   'Nothing is generated and nothing is invented when it is given.')
@click.option('--gpg-email', 'gpg_email', metavar='EMAIL', default=None,
              help='The identity to generate the deployer gpg keypair under. Needed when the '
                   'directory holds no gpg key and --gpg-key was not given; without either there is '
                   'no identity to make one under, and this verb refuses before it writes.')
# A flag and not a write: kubementat adds no remote, so `--git-url` names where the operator should
# push and changes nothing on disk. Inventing one would be a second claim about a repository this
# command was not pointed at.
@click.option('--git-url', 'git_url', metavar='URL', default=None,
              help='Name this remote in the closing instruction to push what was just committed. It '
                   'configures nothing: no remote is added and nothing is pushed.')
# The pair that `initialize` had and this verb now spells. Letting git-crypt commit its own
# collaborator addition is what made the next run read a step as done, so the commit is kubementat's
# and this flag withholds it rather than withholding one of several.
@click.option('--no-commit', 'no_commit', is_flag=True,
              help='Stage everything and commit nothing. The tree is otherwise fully prepared, and '
                   'the first commit is the operator\'s to write.')
@click.option('--force', is_flag=True,
              help='Accept material kubementat did not write, and touch none of it. It is not an '
                   'overwrite: a file that differs from what this verb would write is refused '
                   'either way. It does not lift the refusal of a .gitattributes or .gitignore '
                   'somebody else configured -- that refusal is about a rule and not about material, '
                   'and force was never its remedy.')
def init_command(directory, environment, team, set_answers, encrypted_answers, gpg_key, gpg_email,
                 git_url, no_commit, force):
    """Prepare the directory, print the steps, then say what to do next.

    The library prints nothing on its own, so the verb owns stdout -- and owns it in the order the
    library returned the steps, because that order is the order the plan was applied in and a
    reordered print is a second, prettier account of a write that happened some other way. stdout is
    *only* the step lines: it is the file list, which is why the closing prose never repeats it.

    Everything else is guidance about state and goes to stderr, the same contract
    `warn_cluster_not_addressed` is on. The summary is printed in this order -- the symmetric key when
    this run exported it, then the push, then the deployer key the git host needs, then `kmt install`
    -- and the cluster sentence keeps its place before them, printed only when the file just written
    says so, because `--set CLUSTER_KUBE_CONTEXT=…` is an answer this command can be handed and "is
    null" would then be false about a tree it configured. No line of this prose can hold key
    material: it names paths and never bytes.

    A run that can be refused is refused before it writes. `prepare_configuration_directory` decides
    the keypair's requirement ahead of `git init`, so a bare `config init <dir> --environment E` that
    has no identity to work from leaves no `.git`, which is the promise `config_keys` already made and
    the promise `initialize` broke by refusing four steps in.
    """
    root = Path(directory)

    # Parsed before the library is called: a malformed `KEY=VALUE` is the operator's typo and is
    # answered before anything is decided about the tree on disk.
    answers = tuple(config_init.parse_answer_argument(item, flag='--set')
                    for item in set_answers)
    encrypted = tuple(config_init.parse_answer_argument(item, flag='--encrypted-set')
                      for item in encrypted_answers)

    steps = config_bootstrap.prepare_configuration_directory(
        root, environment, team=team, answers=answers, encrypted_answers=encrypted,
        gpg_key=Path(gpg_key) if gpg_key is not None else None, gpg_email=gpg_email,
        commit=not no_commit, force=force)

    for step in steps:
        click.echo(_step_line(step))

    static_json = root / environment / "static.json"
    if config_init.cluster_is_unaddressed(static_json):
        config_init.warn_cluster_not_addressed(environment, static_json)

    for line in _summary_lines(root, environment=environment, team=team, git_url=git_url,
                               steps=steps):
        click.echo(line, err=True)


def _summary_lines(root, *, environment, team, git_url, steps) -> list[str]:
    """What happens next, in the order it has to happen, and only what happens next.

    The step lines on stdout already are the file list, so nothing here repeats them. The four
    sentences below are the closing summary, and each one exists because the run that printed it cannot
    do the thing for the operator: kubementat adds no remote, pushes nothing, and hands no key to a
    git host.

    **The symmetric key is printed once and only when this run exported it.** The file is the ability
    to read every credential in the cluster, so saying so is the whole point of exporting it and a
    run that skipped the export would be repeating advice about a file it did not create -- which is
    how a warning stops being read. **Nothing here prints key material**; paths only, always.
    """
    lines: list[str] = []

    if any(step.name == config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_STEP and
           step.action == config_init.CREATED
           for step in steps):
        lines.append(f"{root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME} is the ability to read "
                     f"every credential in this cluster: it is mode 0600 and it is gitignored, so it "
                     f"lives in this directory only and never in the repository. Store it somewhere "
                     f"else before the first reinstall, because nothing can regenerate it.")

    if git_url:
        lines.append(f"the commit this run made is local until it is pushed. The remote is "
                     f"{git_url}, and kubementat added nothing to make it one: add that remote and "
                     f"push before anything else clones this directory.")
    else:
        lines.append("the commit this run made is local: no remote was given, no remote was added, "
                     "and nothing was pushed. Add the remote and push it before anything else clones "
                     "this directory.")

    lines.append(f"{root / config_keys.SSH_PUBLIC_NAME} is the deployer key the git host needs read "
                 f"access to, and it lives in the directory just prepared. The private half sits "
                 f"beside it, gitignored.")

    # Two things make this an instruction and not advice: the team is install's second positional and
    # not a flag (`commands/kubementat.py:41`), and install reads a root it is told and does not locate
    # one, so the directory just prepared has to appear in the command. A run that created no team layer
    # prints an install command the operator cannot finish, which is why it says so and names the verb
    # that writes the layer.
    if team:
        lines.append(f"then kmt install {environment} {team} --config-dir {root}, which is the whole of "
                     f"ready to use: nothing else has to write into this tree first.")
    else:
        lines.append(f"then kmt install {environment} <team> --config-dir {root} installs it once a "
                     f"team exists, and this run created none. kmt config add-team {root} "
                     f"{environment} <team> is the verb that writes that layer.")
    return lines


@config.command(name='add-environment',
                 help='Add one environment layer to a configuration directory that already exists: '
                       'its two static files and its two tekton layer directories, and nothing else. '
                       'It copies no templates and writes no answers, and it will not bootstrap a '
                       'tree -- kubementat.yaml says which product the tree is for and only config '
                       'init writes it, so run that first.')
# Two positionals and no flags, and that is the deliverable rather than the minimal shape: the
# directory is what the verb is about (init spells it the same way) and the environment is the one
# thing it cannot be told. There is nothing to answer, so there is no `--set` to take, and no
# resolution flag: this verb creates state and addresses no cluster.
@click.argument('directory')
@click.argument('environment')
def add_environment_command(directory, environment):
    """Add an environment to a tree, then print the steps. The same shape as `init`'s.

    The library decides everything behind this line, including whether refusing is what happens. The
    stdout the verb owns is the step list in the order it was applied, and the only other sentence is
    the cluster warning -- printed under the same condition `init` prints it under, because a new
    environment addresses nothing and an operator who named a context years ago should not be told
    otherwise by a verb that changed nothing about that.
    """
    root = Path(directory)

    steps = config_init.add_environment(root=root, environment=environment)
    for step in steps:
        click.echo(_step_line(step))

    # Spelled the way `init` spells it, so the two verbs that point at a cluster-less layer point at
    # one file and not at two similar-looking ones. Whether the file says so is the library's question.
    static_json = root / environment / "static.json"
    if config_init.cluster_is_unaddressed(static_json):
        config_init.warn_cluster_not_addressed(environment, static_json)


@config.command(name='add-team',
                help='Add one team layer under one environment of a configuration directory that '
                       'already exists: two empty static files and the team\'s four layer '
                       'directories, and nothing else. It needs kmt config add-environment first: a '
                       'team is a layer under one environment, and this verb does not make one.')
# Three positionals, in the order the tree holds them: root, then the environment the team is under,
# then the team. No `--environment` here because a flag would let `--team` arrive without the layer
# it sits on, which is the refusal config init already makes.
@click.argument('directory')
@click.argument('environment')
@click.argument('team')
def add_team_command(directory, environment, team):
    """Add a team to one environment, then print the steps, and nothing else.

    No cluster sentence here, and its absence is the claim: the sentence belongs to the layer that is
    a cluster, and one directory is exactly one cluster (`cluster.py:737`, `cluster.py:898` read the
    environment's file and no other), so a team cannot address one and saying so would be a second,
    wrong answer about the same null.
    """
    steps = config_init.add_team(root=Path(directory), environment=environment, team=team)
    for step in steps:
        click.echo(_step_line(step))


@config.command(name='set',
                help='Answer one key in one layer of a configuration directory that already exists: '
                     'the key lands in that layer\'s file and every other key in it keeps its value '
                     'and its place. It is the first read-modify-write in kubementat and it is not a '
                     'way to make a layer: kmt config init / add-environment / add-team make those. '
                     'The answer is either a value you supply or --generate KEY, and the refusal that '
                     'comes with the second is what makes generation a deliberate act instead of a '
                     'side effect of touching a directory.')
# The shape is `add-team`'s, because the tree holds the layers in this order: root, then environment,
# then team. `--team` is a flag and not a positional for exactly the reason `init` spells it as one:
# a team answer must not be able to arrive without the layer it sits on being spelled. The answer is
# a positional and not a `--set` flag here, because unlike `init` this verb has exactly one answer and
# nothing else it is telling you -- and `set_answers` is `init`'s parameter name, so one flag and one
# positional that take the same `KEY=VALUE` still name the answer the same way to whoever reads the
# parser.
@click.argument('directory')
@click.argument('environment')
# Optional, because `--generate` is the other way to answer a key and this verb has exactly one answer
# either way. Both at once is a command-line question and not a configuration one, so it is refused
# with `UsageError` below rather than settled by the tree on disk.
@click.argument('set_answers', metavar='[KEY=VALUE]', required=False, default=None)
@click.option('--team', default=None,
              help='Put the answer on the team layer instead of the environment layer. The team must '
                   'already exist: kmt config add-team makes it.')
@click.option('--encrypted', is_flag=True,
              help='Answer the encrypted file of the layer rather than the plain one. What belongs '
                   'there is what git-crypt protects: private keys and credentials.')
# A key name and no value, because this flag invents the value and not the operator. `--generate` on a
# key that already answers is refused by the library, and nothing here decides which keys are worth
# generating for: there is no such list, and a flag that kept one would be the second source of truth
# about keys that a rewritten rule forbids.
@click.option('--generate', metavar='KEY', default=None,
              help='Invents a random value for KEY where nothing currently answers it. It refuses '
                   'when the key already answers, and it does not care what kind of key KEY is.')
# The answer spelled as a file instead of as an argument, because "one key per run" is a rule about
# the parser and not about how many answers a tree needs. What a document buys is the case an argv
# cannot serve: a value too long to paste, or a generated credential that would sit in the process
# table for anyone who looks. `--encrypted` is still the flag that says *which* file the answers land
# in, exactly as it does for a `KEY=VALUE`, so a document never re-decides the layer by itself.
# (This verb's surface was spelled as `config set KEY=VALUE` *and* `--from-file`,
# which is the honest reason the flag exists before any lane needed it.)
@click.option('--from-file', 'from_file', metavar='PATH', default=None,
              help='Answer every key of a JSON object, in the order the document lists them. The file '
                   'is how a value that must not sit in a process table gets answered: only the path '
                   'travels, and the value is read where the operator put it.')
def set_command(directory, environment, set_answers, team, encrypted, generate, from_file):
    """Answer one key, print the step, and say nothing else about the tree than the step does.

    `set_answer` (or `generate_answer`, which is its mirror and calls it for the write) decides
    everything behind the lines it returns, including whether refusing is what happens. The parse
    happens before the library is called for the same reason `init` parses first: a malformed
    `KEY=VALUE` is the operator's typo and is answered before anything is decided about the tree on
    disk. The cluster sentence follows only when `--team` was not given, because a team does not
    address a cluster and saying otherwise would be a second, wrong answer about the same null
    (`add-team` refuses on that claim too).

    The generated value is never printed. The step line names the file the answer landed in, and that
    is the whole stdout: echoing the value would put an answer into a terminal buffer that outlives
    the file, and `config show` is the verb that prints answers back.
    """
    root = Path(directory)

    # Both spellings of an answer, and the shape where neither is given, are command-line faults:
    # nothing on disk can fix them, so they answer with `UsageError` and not with `ClickException`
    # (`cli/context.py:150-155` explains the split). Refusing here is before the library, so the
    # tree is untouched either way.
    if from_file is not None and (set_answers is not None or generate is not None):
        raise click.UsageError(
            "--from-file and an answer on the command line were both given, and the document is the "
            "answer. Spell every key inside it instead, or drop the file: this verb does not let one "
            "run write an answer twice, in two orders, with one line describing both.")
    if set_answers is not None and generate is not None:
        raise click.UsageError(
            "an answer and --generate were both given, and this verb writes exactly one answer to one "
            "key. Drop one: spell the value you chose as KEY=VALUE, or drop it and let --generate "
            "invent one for a key that answers nothing.")
    if generate is not None and not generate.strip():
        raise click.UsageError(
            f"--generate was given and no key follows it, so there is no key to invent a value for. "
            f"kmt config set <dir> <environment> --generate KEY [--team T] [--encrypted]")
    if set_answers is None and generate is None and from_file is None:
        raise click.UsageError(
            f"no answer was given, so there is nothing for config set to write. Either answer the key "
            f"yourself, kmt config set <dir> {environment} KEY=VALUE, answer a whole document with "
            f"--from-file PATH, or let kubementat invent one for a key that answers nothing, "
            f"kmt config set <dir> {environment} --generate KEY")

    if generate is not None:
        steps = config_init.generate_answer(root=root, environment=environment, key=generate,
                                            team=team, encrypted=encrypted)
    elif from_file is not None:
        # One `set_answer` per key, and one line per file, because that is what a step means. Decided
        # before the first byte, and said per key: a document that answers one key with `null` is a
        # document that also names the verb that removes it, and a refusal that arrives after three
        # writes is a tree in a state no operator meant.
        steps = ()
        for key, value in _answers_from_file(Path(from_file), root, environment):
            steps += config_init.set_answer(root=root, environment=environment, key=key, value=value,
                                            team=team, encrypted=encrypted)
    else:
        # `--set`'s parser is this verb's parser: split on the first '=', JSON when it parses, raw
        # string when it does not. A boolean stays a boolean because `str()`-ing it is the quoting
        # bug all over.
        key, value = config_init.parse_answer_argument(set_answers)
        steps = config_init.set_answer(root=root, environment=environment, key=key, value=value,
                                       team=team, encrypted=encrypted)
    for step in steps:
        click.echo(_step_line(step))

    # Spelled the way `init` spells it and reached through the same predicate, so the verbs that can
    # leave a cluster-less environment behind point at one file and print one sentence about it. Absent
    # under --team: the team layer is not the layer that reaches a cluster.
    if team is None:
        static_json = root / environment / "static.json"
        if config_init.cluster_is_unaddressed(static_json):
            config_init.warn_cluster_not_addressed(environment, static_json)


@config.command(name='unset',
                help='Take one answer away from one layer of a configuration directory that already '
                     'exists: both files of that layer, because the pair is one answer and clearing '
                     'only the plain file is a change the reader will not see. The layer above then '
                     'answers, which is the whole contract of this verb.')
# Same positional order as every other verb here, and `key` is a bare positional rather than a
# `KEY=VALUE` because there is no value to type: this verb removes an answer and has no opinion about
# what the answer was. `--team` means the same thing it means on `set`, so the two verbs cannot drift
# apart about which layer a flag reaches.
@click.argument('directory')
@click.argument('environment')
@click.argument('key')
@click.option('--team', default=None,
              help='Clear the key from the team layer instead of the environment layer.')
def unset_command(directory, environment, key, team):
    """Clear one key from both files of one layer, print the two steps, and write nothing else.

    No cluster sentence, and that is a claim rather than an oversight: this verb was not asked about
    the cluster keys and prints no opinion about a key it may not have touched. An unset that removes
    `CLUSTER_KUBE_CONTEXT` prints a step that says the file changed, which is the honest statement --
    `kmt environments list` is where the consequence belongs.
    """
    steps = config_init.unset_answer(root=Path(directory), environment=environment, key=key, team=team)
    for step in steps:
        click.echo(_step_line(step))


@config.command(name='show',
                help='Effective configuration for one environment: every key it answers, each one '
                     'tagged default / environment / team and named by the file that declared it. '
                     'It reads and writes nothing, addresses no cluster, and lists the keys that '
                     'answer as defaults, not only the ones a file happens to mention.')
# Root, then environment, then team — the order every verb on this group spells them in. `TEAM` is a
# positional because that is how `show` spells this row and how `add-team` spells the same thing, and not
# because the verb is choosing an edit target: this verb changes nothing, so it has none. No
# --context / --kubeconfig / --config-dir (EXPECTED_CONFIG_COMMAND_PARAMS pins that): the root is
# named as a positional and nothing here resolves a cluster.
@click.argument('directory')
@click.argument('environment')
@click.argument('team', required=False, default=None)
def show_command(directory, environment, team):
    """Every key the tree answers, one per line, sorted by name.

    `config_answers.effective_answers` decides what belongs in the universe and which layer answers
    each key; this function owns only the line. Sorted because the reason a human pipes this is a
    `diff` of two runs, and because a listing whose order depends on which file was read first is a
    listing that cannot be compared to itself.

    The only thing printed is the lines themselves: no step list, which `set` / `unset` print because
    they changed something, and no cluster sentence, because this verb changed nothing about any
    cluster and has no opinion worth printing about one. A missing layer file is refused by the
    library with the sentence `config set` gives, naming the verb that makes the layer.
    """
    for answer in config_answers.effective_answers(Path(directory), environment, team):
        click.echo(_answer_line(answer))


@config.command(name='validate',
                help='Read a configuration tree and say whether it is usable: one verdict, every '
                     'fault named in the same run, warnings printed and not refused. It reads and '
                     'writes nothing, addresses no cluster, and execs nothing — the collision half '
                     'of the answer comes from the same registry the 1:1 check uses.')
# The positional order every verb on this group spells: root, then environment, then team. `TEAM` is
# a positional like `show` spells it (this verb reads, so it has no edit target), and no resolution
# flag is present — validate resolves nothing and `EXPECTED_CONFIG_COMMAND_PARAMS` pins
# that absence rather than trusting a comment about it.
@click.argument('directory')
@click.argument('environment')
@click.argument('team', required=False, default=None)
def validate_command(directory, environment, team):
    """The verdict, in the order it costs the reader something.

    The shape is the one the census and this verb agree on, and no CLI command spelled it before this one
    (collect-then-choose is a library idiom: `helm_utils.py:303` says *all* of the failures rather
    than the first, and validate keeps that promise with a list instead of one message):

    * warnings go to **stderr** first, one per line, in `WARNING: …`'s shape (`utilities.py:66`) —
      always there on a terminal, never inside a pipe, and never deciding the exit code;
    * if there are errors, one `click.ClickException` carries the count and every one of them, and
      ends with what to run. That message *is* the verdict; a second summary after it would be a
      second opinion about the same run;
    * with no errors, stdout gets exactly one line. Silence here would read as a command that
      checked nothing, so the line says which environment was checked and how loud the run was.
    """
    root = Path(directory)
    report = config_validate.validate_tree(root, environment, team)

    for warning in report.warnings:
        click.echo(f"WARNING: {warning.message}", err=True)

    if report.errors:
        listed = "\n".join(f"  {f.code}: {f.message}" for f in report.errors)
        again = f"kmt config validate {root} {environment}" + (f" {team}" if team else "")
        raise click.ClickException(
            f"{len(report.errors)} fault(s) in the configuration tree at {root} for environment "
            f"{environment!r}, every one of them listed rather than only the first:\n{listed}\n"
            f"Fix what they name, then run {again} to get a new verdict.")

    click.echo(f"no errors for environment {environment} ({len(report.warnings)} warnings)")


def _answers_from_file(path: Path, root: Path, environment: str) -> tuple[tuple[str, object], ...]:
    """The answers a `--from-file` document carries, decided before anything is written.

    A document and not a flag, for two reasons that are about the interface and not about parsing.
    Some values cannot be pasted into a shell comfortably (a long key, a block of text with newlines),
    and some of them are credentials the operator does not want to sit in the process table, where
    every `ps`, every shell history and every captured command would have them. The file is what makes
    the answer *not* be a command line; the path is the only thing that travels.

    The document is a JSON object and nothing else. That is not parsimony, it is shape: `set_answer`
    writes `document[key] = value` at the top level of one file, so a list, a scalar, or a nested
    object would be a claim about layers this verb was not told about, and a tree that arrives through
    a document nobody structured has no business guessing at structure to make the run look
    successful. Keys are read in the document's order, which is also the order the step lines appear
    in, because a lane reading its own answers back should see the order it wrote them in.

    Refusals, all before the first byte, each naming the path it was unhappy about:

    * the file cannot be read, because a mistyped path is the operator's and not the tree's problem;
    * the document is not JSON, or is JSON that is not an object, which is the same "the shape is
      wrong" the answer itself would get from `KEY=VALUE`;
    * the document is empty, because an empty document answers nothing and silence here would read as
      a run that applied the file;
    * any key answered with `null`, because `null` is the absence of an answer and belongs to
      `config unset`, exactly as it does for one answer spelled on the command line.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(
            f"--from-file was told to read {path} and could not: {exc}. The document is the answer, so "
            f"this verb wrote nothing.") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise click.ClickException(
            f"{path} is not JSON, so there is no answer in it to write: {exc}. The document is one "
            f"object of key to value, and every key in it lands in one file of the layer this verb "
            f"was pointed at.") from exc
    if not isinstance(document, dict):
        raise click.ClickException(
            f"{path} is JSON that is not an object, and a document answers keys rather than being a "
            f"list or a value of its own. Write it as one object of key to value.") from None
    if not document:
        raise click.ClickException(
            f"{path} is an empty object, which answers nothing. If the answers are not known yet, the "
            f"tree already says so and this verb has nothing to write.")
    for key, value in document.items():
        if value is None:
            raise click.ClickException(
                f"{path} answers {str(key)!r} with null, and null is not an answer: it is how a key "
                f"says it is not answered here. Remove the key from the document, then run "
                f"kmt config unset {root} {environment} {key} to take it back out of the layer above.")
    return tuple((str(key), value) for key, value in document.items())


def _value(value) -> str:
    """One answer as it is printed: a string bare, everything else as JSON.

    `null` and not `None`, because that is how the file that declares it spells it, because
    `config set --set` parses `null` back into exactly that value, and because the lanes read this
    line as their state — a value that prints as Python's spelling of itself is a value that cannot
    be read back without knowing which language wrote it.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _answer_line(answer) -> str:
    """`<KEY> = <value>   (<origin> from <path>)`, and only that shape.

    Two facts this shape carries and nothing else. **The tag is separated by three spaces**, because
    the value is the half a script greps and a single space would let it read as part of the value.
    **A default says which L0 answered**, because "it is a default" alone sends somebody into
    `constants.py` without telling them whether the namespace or the chart-version literal is what
    answers — and those two live in different files for different reasons.
    """
    if answer.origin == config_answers.DEFAULT_ORIGIN:
        tag = f"{config_answers.DEFAULT_ORIGIN} from {config_answers.default_source(answer.key)}"
    else:
        tag = f"{answer.origin} from {answer.source}"
    return f"{answer.key} = {_value(answer.value)}   ({tag})"


def _step_line(step) -> str:
    """One step as one line.

    `kept ... (not kubementat's)` rather than `existed` because the word that matters under `--force`
    is *whose it is*: `created` and `skipped` both say something about kubementat's own write, and
    `kept` says that the byte came from somewhere else and moved nowhere.
    """
    if step.action == config_init.EXISTED:
        return f"kept {step.path} (not kubementat's)"
    return f"{step.action} {step.path}"
