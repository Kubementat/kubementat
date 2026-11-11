"""Every `%`-format string in the harness is a printf-grammar declaration, and printf is hostile.

Three separate lane runs died on string formatting, and every one of them had the same shape: a
format string that Python could not parse, discovered by a VM that had already cost ten minutes. They
are not typos in the usual sense, which is what makes a review-only defence useless here --

* `"printf %s" + ... .format(f=path)` -- `str.format` does not substitute `%` placeholders, so the
  shell got a literal `printf %s` with no argument and appended a blank line. The file looked
  correct, `cat` showed the right thing except the one key, and the controller stayed deployed.
* `"a release ... in %s: %s%s. The class config names is %r%s." % (ns, rel, else, err, cls, note)` --
  six arguments, five conversions. Reads as a green assert, throws as a TypeError.
* `"curl ... -w '\\nKMT_HTTP_STATUS:%{http_code}\\n'" % (a, b)` -- and this is the one worth the file:
  Python parses `%{` as the start of a *named mapping* conversion, not as a literal brace. curl needs
  `%{http_code}`, so the string needs `%%{http_code}`. Two arguments and three conversions, so the
  failure was `TypeError: not enough arguments for format string`, raised from inside the ingress
  feature, which took stage 05 down with it and recorded `assert:routing_ingress` as a bare
  exception instead of a verdict.

None of these are caught by reading the code, because each string is *almost* right, and none of them
are caught by importing it: a format string is only checked when it is applied. So the check is
applied statically, against every `%`-format in the harness, using the same grammar Python uses --
`%%` is an escape, `%` followed by a conversion character is a placeholder, and `%` followed by
anything else is the bug that cost the lane its runs.

The AST walk rather than a grep, because the bug lives in the spacing between lines: an implicitly
concatenated multi-line string is one format string, and a grep reading line by line sees three
innocent ones.
"""

import ast
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "tests" / "e2e" / "harness"

CONVERSION_TYPE = "diouxXeEfFgGcrsa"
FLAGS = "-#0 +"


def _scan(fmt):
    """`[(conversion_text, is_valid)]` for a format string, read left to right the way Python reads it.

    Deliberately not a single regex. The first version used one, and backtracking made `%02d` look
    invalid: the flag class matched `02`, then tried to treat `d` as the offending character, failed,
    and gave the whole rule back -- so the guard reported a legal conversion as a defect while the bug
    it was written against (`%{http_code}`) stayed invisible. A scanner that only ever moves forward
    cannot re-decide what a prefix meant.
    """
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] != "%":
            i += 1
            continue
        if fmt[i + 1:i + 2] == "%":
            i += 2
            continue
        j = i + 1
        if fmt[j:j + 1] == "(":                      # %(name)s -- a named mapping key
            close = fmt.find(")", j)
            if close < 0:
                return out + [(fmt[i:], False)]
            j = close + 1
        while j < len(fmt) and fmt[j] in FLAGS:      # flags
            j += 1
        if j < len(fmt) and fmt[j] == "*":           # width from the arguments
            j += 1
        else:
            while j < len(fmt) and fmt[j].isdigit():
                j += 1
        if j < len(fmt) and fmt[j] == ".":           # precision
            j += 1
            if j < len(fmt) and fmt[j] == "*":
                j += 1
            else:
                while j < len(fmt) and fmt[j].isdigit():
                    j += 1
        if j >= len(fmt) or fmt[j] not in CONVERSION_TYPE:
            out.append((fmt[i:j + 1], False))
            i = j + 1
            continue
        out.append((fmt[i:j + 1], True))
        i = j + 1
    return out


def _placeholders_and_defects(fmt):
    """`(count, [defect, ...])`, where a defect is a `%` that is not a conversion and not `%%`."""
    found = _scan(fmt)
    return sum(1 for _text, ok in found if ok), [text for text, ok in found if not ok]


def _format_strings_in(path):
    """`[(line_number, format_string, argument_count_or_None)]` for one file.

    `argument_count` is None when the right-hand side is not a tuple literal, i.e. when the count
    cannot be known statically and only the grammar is checkable.
    """
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)):
            continue
        if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
            continue
        right = node.right
        count = len(right.elts) if isinstance(right, ast.Tuple) else None
        found.append((node.lineno, node.left.value, count))
    return found


def test_no_harness_format_string_writes_a_conversion_python_cannot_parse():
    """Every `%` is either `%%`, a conversion, or a defect. There is no third option.

    This is the assertion that would have caught all three of the runs' failures except the `.format`
    one, and it catches the class rather than the instance: `%{http_code}`, `%5`, `%q`, an unterminated
    `%(name)` -- all of them land here as a two-second failure instead of a twenty-minute one.
    """
    offenders = []
    for path in sorted(HARNESS.glob("*.py")):
        for lineno, fmt, _count in _format_strings_in(path):
            _n, defects = _placeholders_and_defects(fmt)
            if defects:
                offenders.append("%s:%s -> %s" % (path.name, lineno, defects))
    assert not offenders, \
        "unparseable conversion in a %% format string (it raises only when applied): %s" % (offenders,)


def test_the_argument_count_matches_the_placeholders_whenever_both_are_known():
    """Six arguments into five `%s` is a TypeError waiting for the code path to be reached.

    Only checked when the arguments are a literal tuple and the format string is a literal, because
    that is the only case the count can be derived without running it. It is the case the lane broke
    on, and it is the case that a code review reliably misses: the string and its arguments sit on
    opposite sides of a call, and the reviewer reads them in the wrong order.
    """
    mismatches = []
    for path in sorted(HARNESS.glob("*.py")):
        for lineno, fmt, count in _format_strings_in(path):
            if count is None:
                continue
            placeholders, _defects = _placeholders_and_defects(fmt)
            if placeholders != count:
                mismatches.append("%s:%s has %d placeholder(s) and %d argument(s): %r"
                                  % (path.name, lineno, placeholders, count, fmt[:70]))
    assert not mismatches, \
        "a %% format that raises TypeError when reached: %s" % (mismatches,)


def test_the_harness_never_builds_a_shell_command_with_str_format_alone():
    """`"printf %s" .format(f=path)` is a silent no-op, and the file it writes looks fine.

    Mixing the two formatters is the mistake that let k3s's bundled Traefik stay deployed for two
    lane runs: `.format` filled `{f}`, left `%s` untouched, and the shell printed an empty line. It
    passes `assert:` on the write's exit code because nothing failed, and only the *next* assert,
    reading the cluster rather than the return value, noticed.

    Flagged only where the format string actually carries a `%` conversion, because `{f}`-style
    templates without `%` are harmless and there is no reason to make the harness write every command
    one way.
    """
    mixed = []
    for path in sorted(HARNESS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format"):
                continue
            value = node.func.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            text = value.value
            if "%s" in text.replace("%%", "") or "%r" in text.replace("%%", ""):
                mixed.append("%s:%s" % (path.name, node.lineno))
    assert not mixed, ("str.format applied to a string carrying %%-conversions; format does not "
                       "substitute them, so the shell receives a literal placeholder: %s"
                       % (mixed,))
