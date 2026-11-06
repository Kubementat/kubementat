"""Read-only Kubernetes diagnostics commands, plus the two
helm-deployment sessions that were scripts under `scripts/utilities/helm/`.

These are the kubernetes reads that lived as scripts under
`scripts/utilities/kubernetes/`. Nothing here writes to the cluster, and nothing here
starts `kubectl` for the report: the calls go through the Kubernetes client, and the one
question the client cannot answer alone — whether metrics-server is serving — is answered
gracefully, because a cluster without it is an ordinary cluster, not a traceback.

`service-account-token` is the other read, and the script it replaces
(`scripts/secret_management/retrieve_token_for_service_account.sh`) was the one of the
retired set that had a *use* rather than a caller: a token is a credential, so it is printed
on stdout and nothing else, and the absences it can meet on the way there say so in one line.

`create-secret-from-json` is the write in this file, and it is here for the same reason
`service-account-token` is: the script it replaces (`create_secret_from_json.sh`) was a
secret_management script that a Tekton Task ran. It is the only command here that
changes anything; the four paragraphs above are about the reads and nothing here claims they do not.
"""

import json
from pathlib import Path

import click
from kubernetes.client.exceptions import ApiException

from kubementat import errors
from kubementat.errors import (NamespaceNotFoundError,
                               PodNotFoundError, ServiceAccountTokenNotFoundError)
from kubementat.core.quantities import parse_memory
from kubementat.infrastructure.kubernetes import KubernetesUtils
from cli.context import kmt_context


def _print_pod_table(header, pods):
    click.echo(header)
    if not pods:
        click.echo("  (none)")
        return
    for pod in pods:
        node = pod["node"] or "unscheduled"
        click.echo(f"  {pod['name']:<50} {pod['status'] or '<unknown>':<10} {node}")


def _memory_gigabytes(quantity):
    return f"{quantity / (1024 ** 3):.1f}Gi"


@click.command(name='analyze-deployment',
               help='Analyze a deployment: its pods, their status, and their logs.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@click.argument('deployment')
@kmt_context
def analyze_deployment(ctx, environment, namespace, deployment):
    result = _read(KubernetesUtils(ctx).analyze_deployment, namespace, deployment)
    click.echo(f"Pods for namespace: {namespace}")
    _print_pod_table("POD  STATUS  NODE", result["pods"])
    click.echo(f"\nDeployment: {result['deployment']} (selector {result['selector']})")
    _print_pod_table("MATCHED PODS  STATUS  NODE", result["matched_pods"])
    if result["message"]:
        click.echo(f"\n{result['message']}")
    for pod_name, log in result["logs"].items():
        click.echo(f"\nLogs for {pod_name}:")
        click.echo(log)


@click.command(name='pod-images',
               help='List the pods in a namespace and every image they run.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@kmt_context
def pod_images(ctx, environment, namespace):
    result = _read(KubernetesUtils(ctx).list_pod_images, namespace)
    click.echo(f"Pods for namespace: {namespace}")
    for name in result["pods"]:
        click.echo(f"  {name}")
    click.echo(f"\nUsed Images for namespace: {namespace}")
    for image in result["images"]:
        click.echo(f"  {image}")


@click.command(name='cluster-capacity',
               help='Cluster capacity: requested versus allocatable per node, plus usage '
                    'when metrics-server is installed.')
@click.argument('environment', envvar='ENVIRONMENT')
@kmt_context
def cluster_capacity(ctx, environment):
    result = _read(KubernetesUtils(ctx).cluster_capacity)
    for node in result["nodes"]:
        line = (f"{node['name']}: requested {node['requested_percent']['cpu']}% CPU, "
                f"{node['requested_percent']['memory']}% memory "
                f"(allocatable {node['allocatable']['cpu']:.1f} CPU, "
                f"{_memory_gigabytes(node['allocatable']['memory'])})")
        if node["usage_percent"] is not None:
            line += (f"; usage {node['usage_percent']['cpu']}% CPU, "
                     f"{node['usage_percent']['memory']}% memory")
        click.echo(line)

    average = result["average_requested_percent"]
    if average:
        click.echo(f"\nAverage requested: {average['cpu']}% CPU, "
                   f"{average['memory']}% memory.")

    if not result["metrics"]["available"]:
        click.echo(f"\n{result['metrics']['message']}")

    _print_pod_table("Pod distribution:", result["pod_distribution"])


PVC_TABLE_HEADERS = ("PVC", "NODE", "SIZE", "USED", "AVAILABLE", "USE%")


def _human_size(quantity):
    """A byte count in the largest binary unit that still says something.

    The script's human-readable column ran the sizes through `numfmt --to=iec`, which is not a
    cluster tool and not something this command may shell out for, so the choice of unit is made
    here. It is made in binary units, because that is what the `1024` the summary is measured in
    means, and the wording differs from `numfmt`'s on purpose: it writes `5.0G` and `121K` of the
    same two sizes, and a column a person scans does not need the `.0`. The difference is declared
    in the parity test and not papered over.
    """
    value = float(quantity)
    for unit, factor in (("Pi", 1024 ** 5), ("Ti", 1024 ** 4), ("Gi", 1024 ** 3),
                         ("Mi", 1024 ** 2), ("Ki", 1024)):
        if abs(value) >= factor:
            scaled = value / factor
            return f"{scaled:.0f}{unit}" if scaled.is_integer() else f"{scaled:.1f}{unit}"
    return f"{int(value)}"


def _pvc_cells(row, human):
    """One claim as the six columns of the table, sized the way the flag asked."""
    percentage = row["percentage_used"]
    return [row["name"] or "<unnamed>", row["node"],
            _human_size(row["capacity_bytes"]) if human else str(row["capacity_bytes"]),
            _human_size(row["used_bytes"]) if human else str(row["used_bytes"]),
            _human_size(row["available_bytes"]) if human else str(row["available_bytes"]),
            f"{percentage:.1f}%" if percentage is not None else "n/a"]


def _print_pvc_table(rows, human):
    """The claims as a table, padded the way the script's `column` padded its own.

    Bytes unless `--human`, which is the script's `-h`. The first column is left-aligned and the
    rest right, because the numbers are what a reader scans down a report for.

    The script's headers were `PVC 1K-blocks Used Available Use%` and, human, `PVC Size Used Avail
    Use%` -- both of them produced by an `awk` that had already divided the sizes by 1024, which is
    why the plain one said `1K-blocks` and not `bytes`. The columns below are the bytes the kubelet
    sent, so the header says `SIZE` and `USED` instead of promising a unit that is really there;
    the shortening is the flag's whole job, and the plain form stays pipeable.
    """
    if not rows:
        click.echo("  " + "  ".join(PVC_TABLE_HEADERS))
        click.echo("  (no kubelet reported a claim)")
        return
    table = [_pvc_cells(row, human) for row in rows]
    widths = [max(len(header), max(len(cell[index]) for cell in table))
              for index, header in enumerate(PVC_TABLE_HEADERS)]

    def render(cells):
        line = cells[0].ljust(widths[0])
        for cell, width in zip(cells[1:], widths[1:]):
            line += "  " + cell.rjust(width)
        return line

    click.echo(render(list(PVC_TABLE_HEADERS)))
    for cells in table:
        click.echo("  " + render(cells))


@click.command(name='pvc-usage',
               help='Disk usage of every claim the kubelets report: one row per PVC, per node.\n\n'
                    "Each node's own stats summary is read through the client, so no local proxy "
                    "is started and no size is a guess. Sizes are bytes unless --human (-h) is "
                    "given: that flag is the script's -h, which meant human-readable and not "
                    "help. A node that answers nothing is named, not skipped in silence.")
@click.argument('environment', envvar='ENVIRONMENT')
@click.option('--human', '-h', is_flag=True,
              help='Print the sizes as Ki/Mi/Gi instead of bytes.')
@kmt_context
def pvc_usage(ctx, environment, human):
    # The environment is the first positional here and it is used, not echoed. What "used" means at
    # this line is the honest half: `@kmt_context` resolved the cluster the operator's environment
    # names — `CLUSTER_KUBE_CONTEXT` in `platform_config/<environment>/static.json`, layer 3 of the
    # precedence and the key that exists so a command can say which cluster it reads — and the line
    # naming that cluster is printed on stderr
    # before this body runs, which is the answer every command owes. The old paragraph
    # here justified *ignoring* the environment because no key said where a PVC read went;
    # that key exists now, so the paragraph is gone rather than kept and apologised for.
    #
    # And now it does address it, in the only sense that was ever at issue: the `Context` resolved
    # one cluster, printed which one it was, and `KubernetesUtils(ctx)` builds every client out of
    # that target and no other. There is no path left here where the answer comes from the machine
    # rather than from the command.
    #
    # `_read` is not wrapped around the call, because that wrapper exists to turn a missing
    # namespace into a CLI error and this read has no namespace to be missing. A cluster that cannot
    # be reached fails the way `cluster-capacity` fails, in the client's own words.
    click.echo(f"environment: {environment}")
    result = KubernetesUtils(ctx).list_pvc_usage()
    _print_pvc_table(result["pvcs"], human)
    for message in result["messages"]:
        click.echo(f"\n{message}")


def _read(read, *args):
    """Run a cluster read, turning a missing namespace into a clean CLI error.

    The commands are diagnostics; the cluster answering "no such namespace" is worth a
    one-line answer that names it, not a stack trace ending in `ApiException`.
    """
    try:
        return read(*args)
    except NamespaceNotFoundError as error:
        raise click.ClickException(str(error))


def _attach(action, *args):
    """Hand the terminal over, turning the two absences into one-line answers.

    `deploy-ssh` and `deploy-logs` do not return until the operator is done with them, so all
    three ways they can fail before that are answered here rather than at the next frame of a
    traceback: a namespace that was never created, the deployment that is not installed in it,
    and the `kubectl` that failed on the way in. The last one is the case a distroless image
    creates -- the pod is fine, the binary is not, and kubectl says so in one line that is worth
    more than a Python frame.
    """
    try:
        return action(*args)
    except (NamespaceNotFoundError, PodNotFoundError) as error:
        raise click.ClickException(str(error))
    except errors.CommandExecutionError as error:
        raise click.ClickException(str(error))


@click.command(name='deploy-ssh',
               help='Open a shell in one pod of a helm deployment. Ends when you exit it.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@click.argument('deployment')
@click.argument('container', required=False)
@kmt_context
def deploy_ssh(ctx, environment, namespace, deployment, container):
    # No command option here: what the command runs is /bin/bash, and an escape hatch to any
    # argv would be a way to run anything in any pod from a command whose stated job is looking
    # at one. `KubernetesUtils.open_deployment_shell` takes the command for callers that mean
    # it; the CLI does not.
    _attach(KubernetesUtils(ctx).open_deployment_shell, namespace, deployment, container)


@click.command(name='service-account-token',
               help="Print the token a service account authenticates with.")
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@click.argument('service_account_name')
@kmt_context
def service_account_token(ctx, environment, namespace, service_account_name):
    # `scripts/secret_management/retrieve_token_for_service_account.sh` is gone, so this is the
    # one way to read a token without typing the grep that replaced. The command reads and prints,
    # because that is what the script did: the minting half is already a library call, and it is
    # what `kmt users create-team-account` does before it prints a kubeconfig.
    try:
        token = KubernetesUtils(ctx).get_service_account_token(namespace, service_account_name)
    except (NamespaceNotFoundError, ServiceAccountTokenNotFoundError) as error:
        raise click.ClickException(str(error))
    click.echo(token)


# kubementat's claim on the Secrets it writes. Every other caller sends it
# (`tekton_utils._trigger_labels`, `CLEANUP_JOB_LABELS`); the bash script sent none, so a Secret
# this Task created and a Secret the operator created with `kubectl` were indistinguishable.
SECRET_MANAGEMENT_LABELS = {"managed-by": "kubementat"}


def _json_word(value):
    """What JSON calls a value, so a refusal says it in the language the file is written in."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "value"


def _stringify(value):
    """One JSON scalar as the string a Secret's data entry is.

    A boolean is `true`/`false` and not Python's `True`/`False`, and `null` is `"null"` and not
    `"None"` — both of which is what the shell's `jq tostring` handed `kubectl`, so a key that a
    program inside a pod reads is spelled the same as the one it read before.

    One difference is recorded rather than smoothed over: a float is written the way Python writes
    it, and jq 1.7 writes the literal the file itself used, so `1e10` arrives here as
    `10000000000.0` and there as `1E+10`. Same number, different spelling, and a tfvars-style file
    is not the kind of file that keeps a number in that shape.
    """
    if isinstance(value, str):
        return value
    # `bool` is a subclass of `int` in Python, so it has to be answered before the number branch.
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _string_data_from_json_file(json_file_path):
    """The file's keys as `string_data`, or the one sentence that explains why they are not.

    Flat is the contract, and it is the shape the shell accepted: a document whose value is itself
    an object or an array has no single string to be its entry, and what the shell wrote in that
    slot depended on the `jq` in the image — a nested object went out as `[object Object]` on the
    older ones and as compact JSON on the newer ones, which is one way to spell "whatever the pod
    reads back is decided by a package version". So it is refused, and named.
    """
    try:
        text = Path(json_file_path).read_text(encoding="utf-8")
    except OSError as error:
        raise click.ClickException(
            f"the JSON file {json_file_path} could not be read: "
            f"{error.strerror or error}. Nothing was written to the Secret.")

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise click.ClickException(
            f"the JSON file {json_file_path} does not parse: {error.msg} "
            f"at line {error.lineno}, column {error.colno}. Nothing was written to the Secret.")

    if not isinstance(document, dict):
        raise click.ClickException(
            f"the JSON file {json_file_path} holds a JSON {_json_word(document)} and not an "
            f"object. A Secret's data entries come from the object's keys, so there is nothing to "
            f"take from it. Nothing was written to the Secret.")

    if not document:
        raise click.ClickException(
            f"the JSON file {json_file_path} is an object with no keys. A Secret with no data is "
            f"still a Secret, and this command creates or replaces, so writing it would leave the "
            f"namespace holding a replacement of an existing Secret's data with nothing. Nothing "
            f"was written to the Secret.")

    string_data = {}
    for key, value in document.items():
        if isinstance(value, (dict, list)):
            raise click.ClickException(
                f"'{key}' in {json_file_path} is a JSON {_json_word(value)} and not a value. "
                f"A Secret's data entries are strings and this command does not guess how a "
                f"nested document becomes one. Nothing was written to the Secret.")
        string_data[key] = _stringify(value)
    return string_data


@click.command(name='create-secret-from-json',
               help='Create or replace a Secret whose data entries are the keys of a JSON file.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@click.argument('secret_name')
@click.argument('json_file_path')
@kmt_context
def create_secret_from_json(ctx, environment, namespace, secret_name, json_file_path):
    # What `create_secret_from_json.sh` did is here now, and the Task that ran it calls
    # this instead. Four positionals and no options: three of them are what the script took, and the
    # first is the environment every cluster-touching command has to name. The bash script had
    # neither a team nor an environment, and that is precisely the gap v2 closes — a Secret written
    # by this Task went to whatever `kubectl` was last pointed at. A flag that resolved a namespace
    # out of `platform_config` would still be a second way to say which namespace to write to, so
    # there is none: the namespace is a positional, and the cluster is the one this environment's
    # `static.json` names.
    #
    # The API call is not reimplemented. `KubernetesUtils.create_or_replace_secret` is the
    # primitive, and its create-or-409-replace is the same order `kubectl create --dry-run=client`
    # followed by `kubectl apply` made the script run — with one difference that is a fix and not a
    # detail: the script's `kubectl apply` created or updated whatever the manifest said and kept
    # the fields a previous writer had left, while the replace here sends the object the file
    # describes. Same result for a Secret this command owns, different for one it does not, and
    # that is the sentence the operator needs and not the one the code needed.
    #
    # Two things the script printed and this does not: the YAML it applied, and the table of the
    # namespace's other Secrets after it. The first is the command's business and not the
    # operator's, and the second is a read the command was never asked to do.
    string_data = _string_data_from_json_file(json_file_path)
    try:
        KubernetesUtils(ctx).create_or_replace_secret(
            namespace=namespace,
            secret_name=secret_name,
            # bash built the Secret with `kubectl create secret generic`, and generic is `Opaque`.
            key_type="Opaque",
            string_data=string_data,
            labels=SECRET_MANAGEMENT_LABELS)
    except ApiException as error:
        # The primitive re-raises whatever the cluster answered that was not 409, and that answer
        # is the sentence: a namespace that is not there, a Secret name that is not a name, a
        # controller that cannot write. A traceback on top of it teaches nothing the status line
        # did not.
        raise click.ClickException(
            f"the Secret {secret_name} was not written in namespace {namespace}: {error}")
    click.echo(f"Created secret {secret_name} in namespace {namespace} "
               f"with {len(string_data)} key(s): {', '.join(sorted(string_data))}")


@click.command(name='deploy-logs',
               help="Follow the logs of a helm deployment's pods (Ctrl+C to stop).")
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace')
@click.argument('deployment')
@click.argument('container', required=False)
@click.option('--tail', 'tail_lines', default=None, type=int,
              help='Start this many lines from the end instead of from the beginning.')
@kmt_context
def deploy_logs(ctx, environment, namespace, deployment, container, tail_lines=None):
    # The timestamps the script always asked for are on in the primitive and not a flag here,
    # for the same reason `--follow` is not one: a log line that says when is the half of a
    # "why did it restart twice" answer that is free.
    _attach(KubernetesUtils(ctx).stream_deployment_logs, namespace, deployment, container,
            tail_lines)
