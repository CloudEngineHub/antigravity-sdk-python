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

"""Example demonstrating multi-agent workflows in the Google Antigravity SDK.

This script demonstrates two ways to author and execute deterministic
multi-agent workflows using `beta.workflows` and `agent.beta.run_workflow`:
  1. `@beta.workflows.define` Decorator: Authoring a typed Python function using
     `workflows.phase`, `workflows.log`, `workflows.call_tool`,
     `workflows.parallel`, `workflows.args`, and `workflows.agent` (both
     single-turn and multi-turn `WorkflowAgent` with `schema=...` structured
     JSON output and custom `types.SubagentConfig` roles), validated at
     definition time and executed via
     `await agent.beta.run_workflow(security_audit_workflow, args=...)`.
  2. Standalone Workflow Script File: Executing a `.py` workflow script on disk
     that runs a `pipeline` across multiple items via
     `await agent.beta.run_workflow(script_path=script_path, description=...)`.

To run:
  python workflows.py

Criteria for correct script performance:
  1. The script exits cleanly with return code 0 (no unhandled exceptions).
  2. Workflow 1 (`security_audit_workflow`) reads target files via
     `workflows.call_tool("read_file", ...)`, runs parallel `file_auditor`
     agents, conducts a 2-turn conversation with the `lead_synthesizer`
     `WorkflowAgent` (structured summary turn followed by a remediation turn),
     and prints a non-empty workflow output and structured progress summary.
  3. Workflow 2 (script file execution) runs the sequential pipeline script
     from disk and prints a non-empty workflow output and structured progress
     summary.
"""

import asyncio
import pathlib
import tempfile
import textwrap

from google.antigravity import Agent
from google.antigravity import beta
from google.antigravity import LocalAgentConfig
from google.antigravity import types
from google.antigravity.hooks import policy

workflows = beta.workflows


@workflows.define
async def security_audit_workflow() -> None:
  """Reads source files via call_tool, audits in parallel, and synthesizes in 2 turns."""
  workflows.phase("Read & Audit Files")

  async def read_and_audit(path: str):
    content = await workflows.call_tool("read_file", Path=path)
    # `type_name` selects the `SubagentConfig(name="file_auditor")` registered
    # on `LocalAgentConfig(subagents=[...])`; `role` is a per-call display
    # label shown in workflow progress logs for each parallel invocation.
    return await workflows.agent(
        f"Audit `{path}` in one concise sentence naming the vulnerability:\n"
        f"{content}",
        schema={"type": "string"},
        role=f"Audit {path.rsplit('/', 1)[-1]}",
        type_name="file_auditor",
    )

  findings = await workflows.parallel(workflows.args["paths"], read_and_audit)

  workflows.phase("Multi-Turn Executive Synthesis")
  combined = "\n".join(str(f).strip() for f in findings)
  lead = workflows.agent(
      "You are the lead security synthesizer.",
      role="Lead Synthesizer",
      type_name="lead_synthesizer",
  )
  summary = await lead(
      f"Summarize these security findings:\n{combined}",
      schema={
          "type": "object",
          "properties": {
              "total_issues": {"type": "integer"},
              "highest_severity": {"type": "string"},
              "summary": {"type": "string"},
          },
          "required": ["total_issues", "highest_severity", "summary"],
      },
  )
  remediation = await lead(
      "Now propose a concise 1-sentence remediation plan for the"
      " highest-severity issue from your summary.",
      schema={"type": "string"},
  )
  workflows.log(
      f"Audit complete: {summary['total_issues']} issues"
      f" (highest={summary['highest_severity']}): {summary['summary']}"
  )
  workflows.log(f"Remediation plan: {remediation}")


def _print_workflow_result(title: str, result: beta.WorkflowResult) -> None:
  """Prints the metadata and final output of a workflow run."""
  print(f"\n  --- {title} ---")
  print(f"  Script Path: {result.script_path}")
  print(f"  Description: {result.description}")
  print(f"  Tool Output:\n{textwrap.indent(result.output.strip(), '    ')}")
  if result.response_text.strip():
    print(f"  Response:    {result.response_text.strip()}")


async def run_decorated_workflow(tmpdir: str) -> None:
  """Runs a `@workflows.define` workflow with call_tool and a multi-turn agent."""
  print(
      "\n=== 1. @workflows.define (call_tool + Parallel Fan-Out + Multi-Turn"
      " Agent) ==="
  )

  auth_path = pathlib.Path(tmpdir) / "auth.py"
  auth_path.write_text(
      "jwt.decode(token, options={'verify_signature': False})\n",
      encoding="utf-8",
  )
  db_path = pathlib.Path(tmpdir) / "db.py"
  db_path.write_text(
      "cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')\n",
      encoding="utf-8",
  )

  file_auditor = types.SubagentConfig(
      name="file_auditor",
      description="Audits a code snippet for security vulnerabilities.",
      system_instructions=(
          "You are a concise security auditor. Identify the vulnerability in"
          " the provided snippet in one sentence."
      ),
      capabilities=types.SubagentCapabilities(enabled_tools=[]),
  )
  lead_synthesizer = types.SubagentConfig(
      name="lead_synthesizer",
      description="Synthesizes security findings across multiple turns.",
      system_instructions=(
          "You are a security lead. Combine the audit findings and respond"
          " with the requested schema on each turn."
      ),
      capabilities=types.SubagentCapabilities(enabled_tools=[]),
  )

  config = LocalAgentConfig(
      workspaces=[tmpdir],
      app_data_dir=tmpdir,
      capabilities=types.CapabilitiesConfig(
          enabled_tools=[
              types.BuiltinTools.RUN_WORKFLOW,
              types.BuiltinTools.VIEW_FILE,
          ],
      ),
      subagents=[file_auditor, lead_synthesizer],
      policies=[policy.allow_all()],
  )

  async with Agent(config) as my_agent:
    result = await my_agent.beta.run_workflow(
        security_audit_workflow,
        args={"paths": [str(auth_path), str(db_path)]},
    )
    _print_workflow_result("Decorated Workflow Result", result)


async def run_script_file_workflow(tmpdir: str) -> None:
  """Runs a standalone `.py` workflow script from disk."""
  print("\n=== 2. Standalone Workflow Script File (Sequential Pipeline) ===")

  script_path = pathlib.Path(tmpdir) / "release_checklist_workflow.py"
  script_path.write_text(
      textwrap.dedent("""\
          phase("Sequential Verification")
          checks = ["unit_tests", "lint_check"]
          results = await pipeline(
              checks,
              lambda item: agent(
                  f"Confirm check '{item}' passed in one short sentence.",
                  schema={"type": "string"},
                  role=f"Verify {item}",
              ),
          )
          for item_name, res in zip(checks, results):
              log(f"{item_name}: {str(res).strip()}")
      """),
      encoding="utf-8",
  )

  config = LocalAgentConfig(
      workspaces=[tmpdir],
      app_data_dir=tmpdir,
      capabilities=types.CapabilitiesConfig(
          enabled_tools=[types.BuiltinTools.RUN_WORKFLOW],
      ),
      policies=[policy.allow_all()],
  )

  async with Agent(config) as my_agent:
    result = await my_agent.beta.run_workflow(
        script_path=script_path,
        description="Run the sequential release verification checklist",
    )
    _print_workflow_result("Script File Workflow Result", result)


async def main() -> None:
  with tempfile.TemporaryDirectory() as tmpdir:
    await run_decorated_workflow(tmpdir)
    await run_script_file_workflow(tmpdir)


if __name__ == "__main__":
  asyncio.run(main())
