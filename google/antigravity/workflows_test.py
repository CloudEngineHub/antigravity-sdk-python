# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from absl.testing import absltest
from google.antigravity import beta
from google.antigravity import workflows


class WorkflowsTest(absltest.TestCase):

  def test_beta_workflows_export_and_qualifier_stripping(self):
    self.assertIs(beta.workflows, workflows)
    self.assertIn("workflows", beta.__all__)

    @beta.workflows.define
    async def beta_qualified_pipeline():
      """Pipeline using beta.workflows.<primitive>."""
      beta.workflows.phase("Discover")
      res = await beta.workflows.agent(
          "Check status", schema={"type": "string"}
      )
      beta.workflows.log(res)

    self.assertIsInstance(beta_qualified_pipeline, workflows.WorkflowDefinition)
    self.assertNotIn("beta.workflows", beta_qualified_pipeline.source)
    self.assertIn("phase('Discover')", beta_qualified_pipeline.source)
    self.assertIn(
        "res = await agent('Check status', schema={'type': 'string'})",
        beta_qualified_pipeline.source,
    )
    self.assertIn("log(res)", beta_qualified_pipeline.source)

  def test_define_with_qualified_primitives(self):
    @workflows.define
    async def audit_pipeline():
      """Audit modules in parallel."""
      workflows.phase("Discover")
      workflows.log("Starting discovery")
      modules = await workflows.agent(
          "List modules",
          schema={"type": "array", "items": {"type": "string"}},
      )
      workflows.phase("Analyze")
      results = await workflows.parallel(
          modules,
          lambda item: workflows.agent(
              f"Analyze {item}",
              schema={"type": "string"},
              role="Analyzer",
          ),
      )
      summary = await workflows.pipeline(
          results,
          lambda item: workflows.agent(
              f"Summarize {item}",
              schema={"type": "string"},
          ),
      )
      workflows.log(summary)

    self.assertIsInstance(audit_pipeline, workflows.WorkflowDefinition)
    self.assertEqual(audit_pipeline.name, "audit_pipeline")
    self.assertEqual(audit_pipeline.description, "Audit modules in parallel.")
    self.assertNotIn("workflows.phase", audit_pipeline.source)
    self.assertIn("phase('Discover')", audit_pipeline.source)
    self.assertIn("await parallel(modules,", audit_pipeline.source)
    self.assertIn("await pipeline(results,", audit_pipeline.source)

  def test_define_allows_nested_async_helper_with_return(self):
    @workflows.define
    async def pipeline_with_helper():
      async def fix(bug: str):
        test = await workflows.agent(
            f"Write test for {bug}",
            schema={"type": "string"},
            role=f"Test {bug}",
        )
        return await workflows.agent(
            f"Fix {bug} using {test}",
            schema={"type": "string"},
            role=f"Fix {bug}",
        )

      fixes = await workflows.pipeline(["BUG-1.md", "BUG-2.md"], fix)
      for f in fixes:
        workflows.log("fixed:", f)

    self.assertIn("async def fix(", pipeline_with_helper.source)
    self.assertIn("return await agent(", pipeline_with_helper.source)
    self.assertIn(
        "fixes = await pipeline(['BUG-1.md', 'BUG-2.md'], fix)",
        pipeline_with_helper.source,
    )

  def test_define_with_unqualified_primitives(self):
    phase = workflows.phase
    log = workflows.log
    agent = workflows.agent

    @workflows.define
    async def simple_pipeline():
      phase("Step 1")
      res = await agent("Run step 1", schema={"type": "string"})
      log("Done:", res)

    self.assertEqual(simple_pipeline.name, "simple_pipeline")
    self.assertEqual(simple_pipeline.description, "")
    self.assertIn("phase('Step 1')", simple_pipeline.source)
    self.assertIn(
        "res = await agent('Run step 1', schema={'type': 'string'})",
        simple_pipeline.source,
    )
    self.assertIn("log('Done:', res)", simple_pipeline.source)

  def test_define_preserves_multiline_strings_and_one_liner(self):
    phase = workflows.phase
    log = workflows.log

    @workflows.define
    def multiline_wf():
      prompt = """Line 1
Line 2"""
      log(prompt)

    ns: dict[str, object] = {}
    captured: list[str] = []
    ns["log"] = captured.append
    exec(multiline_wf.source, ns)  # pylint: disable=exec-used
    self.assertEqual(captured, ["Line 1\nLine 2"])

    @workflows.define
    def one_liner():
      phase("SingleLine")  # pylint: disable=multiple-statements

    self.assertEqual(one_liner.source.strip(), "phase('SingleLine')")

  def test_define_preserves_trailing_expression_statements(self):
    @workflows.define
    async def trailing_agent_expr():
      workflows.phase("Start")
      await workflows.agent("Run check", schema={"type": "string"})

    lines = [
        line.strip()
        for line in trailing_agent_expr.source.splitlines()
        if line.strip()
    ]
    self.assertEqual(
        lines,
        [
            "phase('Start')",
            "await agent('Run check', schema={'type': 'string'})",
        ],
    )

  def test_validate_rejects_print_calls(self):
    with self.assertRaisesRegex(
        workflows.WorkflowError,
        r"print\(\) is not allowed in workflow scripts; use"
        r" workflows\.log\(\.\.\.\) to emit output",
    ):

      @workflows.define
      def with_print():
        workflows.phase("Step")
        print("hello")

      del with_print

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        r"print\(\) is not allowed in workflow scripts; use"
        r" workflows\.log\(\.\.\.\) to emit output",
    ):

      @workflows.define
      async def with_nested_print():
        async def helper(item):
          print(item)
          return await workflows.agent(item, schema={"type": "string"})

        await workflows.pipeline(["a"], helper)

      del with_nested_print

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        r"print\(\) is not allowed in workflow scripts; use"
        r" workflows\.log\(\.\.\.\) to emit output",
    ):
      workflows.validate_workflow_source("import builtins\nbuiltins.print(1)\n")

  def test_validate_rejects_return_and_yield(self):
    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "return/yield statements are not allowed in workflow scripts",
    ):

      @workflows.define
      def with_return():
        workflows.phase("Step")
        return "done"

      del with_return

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "return/yield statements are not allowed in workflow scripts",
    ):
      workflows.validate_workflow_source("yield 'x'\n")

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "return/yield statements are not allowed in workflow scripts",
    ):
      workflows.validate_workflow_source("def gen():\n  yield 1\n")

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "return/yield statements are not allowed in workflow scripts",
    ):
      workflows.validate_workflow_source("def gen_from():\n  yield from [1]\n")

  def test_define_with_call_tool_multi_turn_agent_and_args(self):
    @workflows.define
    async def advanced_workflow():
      """Workflow using call_tool, ToolError, multi-turn agent, args, and imports."""
      import json  # pylint: disable=g-import-not-at-top

      target = workflows.args.get("target", "README.md")
      workflows.phase("Read")
      try:
        raw = await workflows.call_tool("read_file", Path=target)
      except workflows.ToolError as err:
        workflows.log(err.tool, err.message)
        raw = "{}"

      reviewer = workflows.agent(
          "You are a code reviewer.",
          role="Code Reviewer",
      )
      attempts = 0
      while attempts < 2:
        verdict = await reviewer(
            f"Review {json.dumps(raw)}",
            schema={"type": "string"},
        )
        workflows.log(verdict)
        attempts += 1
      del reviewer

    self.assertIn("import json", advanced_workflow.source)
    self.assertIn(
        "target = args.get('target', 'README.md')", advanced_workflow.source
    )
    self.assertIn(
        "raw = await call_tool('read_file', Path=target)",
        advanced_workflow.source,
    )
    self.assertIn("except ToolError as err:", advanced_workflow.source)
    self.assertIn(
        "reviewer = agent('You are a code reviewer.', role='Code Reviewer')",
        advanced_workflow.source,
    )
    self.assertIn("while attempts < 2:", advanced_workflow.source)
    self.assertIn("verdict = await reviewer(", advanced_workflow.source)

  def test_validate_rejects_syntax_error(self):
    with self.assertRaisesRegex(
        workflows.WorkflowError, "workflow syntax error"
    ):
      workflows.validate_workflow_source("def broken(:\n")

  def test_define_rejects_required_arguments(self):
    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "workflow functions cannot require positional/keyword arguments",
    ):

      @workflows.define  # type: ignore[arg-type]
      def parameterized_wf(target: str):
        workflows.phase(target)

      del parameterized_wf

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "workflow functions cannot require positional/keyword arguments",
    ):

      @workflows.define  # type: ignore[arg-type]
      def wf_with_default(branch: str = "main"):
        workflows.phase(branch)

      del wf_with_default

    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "workflow functions cannot require positional/keyword arguments",
    ):

      @workflows.define  # type: ignore[arg-type]
      def wf_with_varargs(*args, **kwargs):
        del args, kwargs
        workflows.phase("Step")

      del wf_with_varargs

  def test_define_rejects_docstring_only_function(self):
    with self.assertRaisesRegex(
        workflows.WorkflowError,
        "workflow function body cannot be empty",
    ):

      @workflows.define
      def docstring_only_wf():
        """Only a docstring, no statements."""

      del docstring_only_wf

  def test_primitives_raise_outside_runtime(self):
    with self.assertRaises(TypeError):
      workflows.log()
    with self.assertRaisesRegex(RuntimeError, "workflows.log"):
      workflows.log("hello")
    with self.assertRaisesRegex(RuntimeError, "workflows.phase"):
      workflows.phase("Explore")
    with self.assertRaisesRegex(RuntimeError, "workflows.agent"):
      workflows.agent("Prompt", schema={"type": "string"})
    with self.assertRaises(ValueError):
      workflows.call_tool("")
    with self.assertRaisesRegex(RuntimeError, "workflows.call_tool"):
      workflows.call_tool("read_file", Path="README.md")
    with self.assertRaisesRegex(RuntimeError, "workflows.parallel"):
      workflows.parallel(
          ["a"], lambda x: workflows.agent(x, schema={"type": "string"})
      )
    with self.assertRaisesRegex(RuntimeError, "workflows.pipeline"):
      workflows.pipeline(
          ["a"], lambda x: workflows.agent(x, schema={"type": "string"})
      )
    stub_agent = workflows.WorkflowAgent()
    with self.assertRaisesRegex(RuntimeError, "workflows.agent"):
      stub_agent("Turn prompt", schema={"type": "string"})
    with self.assertRaisesRegex(RuntimeError, "workflows.agent"):
      stub_agent.__await__()

    tool_err = workflows.ToolError("read_file", "permission denied")
    self.assertIsInstance(tool_err, workflows.WorkflowError)
    self.assertEqual(tool_err.tool, "read_file")
    self.assertEqual(tool_err.message, "permission denied")
    self.assertEqual(str(tool_err), "read_file: permission denied")


if __name__ == "__main__":
  absltest.main()
