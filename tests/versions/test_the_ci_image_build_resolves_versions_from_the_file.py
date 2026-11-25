"""The CI image takes its versions from `versions.yaml` — the Dockerfile keeps no defaults.

The Dockerfile is the consumer that used to own its numbers as `ARG` defaults, and `build.sh` is
the caller that resolves the file's `ci_image:` block into build args instead. Both halves of the
chain are product facts: an `ARG NAME=default` line is a second owner a bare `docker build` would
answer to with stale pins, and an arg the build never passes is the same number missing.
"""

import re

from asset_paths import REPO_ROOT
from kubementat.core import versions

IMAGE_BUILD = REPO_ROOT / "docker_files" / "ci_images" / "ubuntu_ci_minimal"
DOCKERFILE = IMAGE_BUILD / "Dockerfile"
BUILD_SCRIPT = IMAGE_BUILD / "build.sh"

# An `ARG` that declares its name and nothing else. The default-carrying shape `ARG NAME=value` is
# exactly what the build must not have: it is the version the file no longer owns, written down.
ARG_DECLARATION = re.compile(r"^ARG (?P<name>[A-Z][A-Z0-9_]*)$")


def test_every_version_arg_the_ci_image_dockerfile_declares_arrives_from_the_build():
    """No `ARG` carries a value, and the names are exactly the keys `versions.yaml` answers.

    The set equality is the drift guard the manual check did once by hand: a rename on either side
    — a key the build never passes, an arg the file no longer names — shows here and not in an
    image that builds quietly with an empty version.
    """
    declarations = [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
                    if line.startswith("ARG ")]
    assert declarations, "the Dockerfile declares no ARG lines, so this proves nothing"

    defaulted = [line for line in declarations if not ARG_DECLARATION.match(line)]
    assert defaulted == [], \
        "an ARG carries its own default, so a bare docker build answers with it: " + " ".join(defaulted)

    names = {ARG_DECLARATION.match(line).group("name") for line in declarations}
    assert names == set(versions.ci_image_args()), (
        f"the build's args and the file's ci_image block disagree "
        f"(only in the Dockerfile: {sorted(names - set(versions.ci_image_args()))}, "
        f"only in the file: {sorted(set(versions.ci_image_args()) - names)})")


def test_the_ci_image_build_passes_every_resolved_arg_to_every_image_build():
    """`build.sh` spells no version itself: it takes the whole set through the print interface
    and forwards it to every `docker buildx build`, so no invocation builds without the answer."""
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "--print-build-args" in script, \
        "the script resolves versions without the interface that owns the file's block"

    build_lines = [line for line in script.splitlines()
                   if re.match(r"^\s*docker buildx build", line)]
    assert build_lines, "the script builds no images, so there is nothing to pass args to"

    missing = [line for line in build_lines if "${BUILD_ARGS[@]}" not in line]
    assert missing == [], \
        "a build runs without the resolved versions, so it answers for itself: " + " || ".join(missing)
