"""`TektonUtils` — the constructor, and nothing else.

Every group of work this class used to hold is now a module of
`infrastructure/tekton/`: `generation.py` (P4-1), `preflight.py` (P4-2), `runs.py` (P4-3),
`install.py` (P4-4) and `triggers.py` (P4-5). What is left is `__init__`, which is the part of the
class the five modules did not take: `self.ctx`, the client built from it, the environment, the
configuration, the team the constructor refuses a run without, and the namespace that read is
what the refusal pays for.

Two facts about this file a reader has to be given and cannot infer from the constructor below.

* **Nothing here calls a moved name.** P4-4 left the file with `generation` on its import line
  because `TektonUtils` still reached one of its helpers; P4-5 moved the last of that, so no name
  is imported from inside the package at all, by any spelling. A `from … import` of a moved name
  would be a second binding and the suite patches by module object, so it is not a way to keep an
  import "for symmetry".
* **The class is still constructible on purpose.** Sixteen test call sites build
  `TektonUtils(a_context)` and then call a function, and the constructor is what supplies the team
  refusal those sites still depend on. P4-6 dissolves it — which is also where the refusal sentence
  and the `config.get('team_static')` read behind it land somewhere else, and where the three keys
  of the source-reading tests retarget. Until then this file is a constructor, a refusal, and the
  one import the constructor needs.
"""

from kubementat.infrastructure.kubernetes import KubernetesUtils


class TektonUtils:
    def __init__(self, ctx):
        # One `Context` and nothing else, the shape the other service classes have.
        # `self.ctx` is kept because it is what a second KubernetesUtils object is built from --
        # the same object the old `Automation(self.ctx)` built -- and building a second `Context`
        # to hand over would resolve "which cluster" a second time for one method, which is what
        # one `Context` is there to prevent.
        self.ctx = ctx
        self.kubernetes_utils = KubernetesUtils(ctx)
        self.environment = ctx.environment
        # The team is not a path segment here, it is the namespace every one of this class's
        # objects is written into: `PIPELINE_NAMESPACE` is read out of `team_static` two lines
        # below, and `require_team` is what says a run without a team has no such namespace.
        self.team = ctx.require_team("the namespace the Tekton objects live in")
        self.config = ctx.config
        self.pipeline_namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
