# Acceptance Gates

Before a candidate can be approved in a later change, require:

1. A typed request/response schema, declared risk, side effects, postconditions, and error codes.
2. No hardcoded port, project, design, object ID, operation value, local path, credential, or approval token.
3. Positive fixtures across representative AEDT/API versions, not only the source trace.
4. Negative tests for missing capability, ambiguous targets, stale previews, rejected or replayed approvals, execution failure, failed readback, and rollback failure.
5. Existing Harness precedence and compatibility tests remain green.
6. Human review of the generated patch followed by a normal code change, test run, and release process.

The promoter never applies the patch, changes the running MCP server, commits code, or marks the candidate approved.
