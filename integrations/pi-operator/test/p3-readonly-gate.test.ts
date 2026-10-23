import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { test } from "node:test";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const repositoryRoot = resolve(packageRoot, "..", "..");
const schemaRoot = resolve(repositoryRoot, "schemas", "operator", "v1");
const python = process.env.AEDT_AGENT_PYTHON ?? resolve(repositoryRoot, ".venv", "Scripts", "python.exe");
const execFileAsync = promisify(execFile);
type RecordedFixtureManifest = {
  fixture_id: string;
  inventory_digest: string;
  approval: { approval_id: string; mission_id: string };
  fixture_observations: { node_run_count: number; artifact_count: number; pending_approval_count: number };
  recorded_missions: Array<{ case_id: string; mission_id: string; graph_run_id: string; template_id: string; expected_status: string; execution_profile: string; inputs: Record<string, unknown> }>;
};

test("P3 readonly gate: Pi observes ten heterogeneous recorded scenarios without database mutation", async () => {
  const workspace = await mkdtemp(resolve(tmpdir(), "aedt-p3-gate-"));
    const database = resolve(workspace, "mission.db");
  try {
    const manifest = JSON.parse(await readFile(resolve(packageRoot, "resources", "p3-recorded-manifest.json"), "utf8")) as RecordedFixtureManifest;
    assert.equal(manifest.recorded_missions.length, 10);
    assert.equal(new Set(manifest.recorded_missions.map((item) => item.case_id)).size, 10);
    assert.equal(new Set(manifest.recorded_missions.map((item) => item.template_id)).size, 5);
    assert.ok(manifest.recorded_missions.every((item) => item.execution_profile === "safe-recorded"));
    assert.deepEqual(manifest.fixture_observations, { node_run_count: 101, artifact_count: 11, pending_approval_count: 1 });
    const fixture = await seedStaticGraphs(database, manifest);
    assert.equal(fixture.inventoryDigest, manifest.inventory_digest);
    await checkpointStaticDatabase(database);
    const before = await databaseHashes(database);
    const { stdout } = await execFileAsync(
      process.execPath,
      [
        resolve(packageRoot, "dist", "scripts", "run-readonly-gate.js"),
        "--db",
        database,
        "--schema-root",
        schemaRoot,
      ],
      { cwd: packageRoot, env: { ...process.env, AEDT_AGENT_PYTHON: python } },
    );
    const report = JSON.parse(stdout) as { status: string; scenario_count: number; observed_operations: string[] };
    assert.equal(report.status, "passed");
    assert.equal(report.scenario_count, 10);
    assert.equal(new Set(report.observed_operations).size, 9);
    await assertSessionRestart(database, manifest);
    assert.deepEqual(await databaseHashes(database), before);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

async function assertSessionRestart(database: string, manifest: RecordedFixtureManifest): Promise<void> {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-p3-restart-"));
  try {
    const base = [
      resolve(packageRoot, "dist", "scripts", "run-session-restart-gate.js"),
      "--db", database, "--schema-root", schemaRoot, "--session-dir", directory,
      "--session-id", "restart-session", "--graph-run-id", manifest.recorded_missions[0]!.graph_run_id,
    ];
    const environment = { ...process.env, AEDT_AGENT_PYTHON: python };
    for (const phase of ["init", "resume"] as const) {
      const { stdout } = await execFileAsync(process.execPath, [...base, "--phase", phase], { cwd: packageRoot, env: environment });
      assert.equal((JSON.parse(stdout) as { status: string; phase: string }).phase, phase);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function seedStaticGraphs(database: string, manifest: RecordedFixtureManifest): Promise<{ inventoryDigest: string }> {
  const program = [
    "import json, sqlite3",
    "from pathlib import Path",
    "from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path",
    "from aedt_agent.agent.runtime_factory import create_runtime_without_workers",
    "from aedt_agent.agent.mission import ApprovalRequest, ArtifactManifest, GraphRunRecord, GraphRunStatus, MissionRecord, NodeRunRecord",
    "import hashlib",
    `database = Path(${JSON.stringify(database)})`,
    "runtime = create_runtime_without_workers(database)",
    `fixture = json.loads(${JSON.stringify(JSON.stringify(manifest))})`,
    "inventory = []",
    "for index, case in enumerate(fixture['recorded_missions']):",
    "    mission = runtime.store.create_mission(MissionRecord.create(case['mission_id'], f'recorded readonly gate {index}', []))",
    "    template = load_graph_template(resolve_template_path(case['template_id']))",
    "    graph = runtime.store.create_graph_run(GraphRunRecord.create(case['graph_run_id'], mission.mission_id, template.template_id, template.version, mission.plan_version, template_snapshot=template.to_json_dict(), initial_payload={'execution_profile': case['execution_profile'], **case['inputs']}))",
    "    runtime.store.update_graph_run_status(graph.graph_run_id, GraphRunStatus(case['expected_status']), error={'code': 'recorded_failure'} if case['expected_status'] == 'failed' else None)",
    "    inventory.append({'case_id': case['case_id'], 'mission_id': mission.mission_id, 'graph_run_id': graph.graph_run_id, 'template_id': template.template_id, 'template_digest': hashlib.sha256(json.dumps(template.to_json_dict(), ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'expected_status': case['expected_status'], 'execution_profile': case['execution_profile'], 'inputs_digest': hashlib.sha256(json.dumps(case['inputs'], ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()})",
    "first = fixture['recorded_missions'][0]",
    "for index in range(101): runtime.store.create_node_run(NodeRunRecord.create(f'recorded-node-{index}', first['graph_run_id'], first['mission_id'], f'node-{index}', 'observer', 'agent', index, {'large_bounded_marker': 'x' * 4096}))",
    "for index in range(11): runtime.store.create_artifact_manifest(ArtifactManifest.create(f'model-artifact-{index}', first['mission_id'], 'recorded', 'gate', f'virtual://model/{index}', 'model_summary', 'a' * 64, 1024 + index))",
    "approval = fixture['approval']",
    "runtime.store.create_approval(ApprovalRequest.create(approval['approval_id'], approval['mission_id'], 'recorded gate approval', [{'id': 'observe', 'label': 'Observe only'}]))",
    "inventory.append({'fixture_observations': {'node_run_count': len(runtime.store.list_node_runs(first['graph_run_id'])), 'artifact_count': len(runtime.store.list_artifact_manifests(first['mission_id'])), 'pending_approval_count': len(runtime.store.list_approvals(first['mission_id']))}})",
    "digest = 'sha256:' + hashlib.sha256(json.dumps(inventory, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()",
    "print(json.dumps({'inventoryDigest': digest}))",
  ].join("\n");
  const { stdout } = await execFileAsync(python, ["-c", program], { cwd: repositoryRoot });
  return JSON.parse(stdout) as { inventoryDigest: string };
}

async function checkpointStaticDatabase(database: string): Promise<void> {
  const program = [
    "import sqlite3",
    `database = ${JSON.stringify(database)}`,
    "connection = sqlite3.connect(database, isolation_level=None)",
    "connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')",
    "connection.close()",
    "connection = sqlite3.connect(database, isolation_level=None)",
    "connection.execute('PRAGMA journal_mode=DELETE')",
    "connection.close()",
  ].join("\n");
  await execFileAsync(python, ["-c", program], { cwd: repositoryRoot });
}

async function databaseHashes(database: string): Promise<Record<string, string>> {
  const files = [database, `${database}-wal`, `${database}-shm`, `${database}-journal`];
  const hashes: Record<string, string> = {};
  for (const file of files) {
    try {
      hashes[file] = createHash("sha256").update(await readFile(file)).digest("hex");
    } catch (error: unknown) {
      if (!(error instanceof Error) || !("code" in error) || error.code !== "ENOENT") throw error;
    }
  }
  return hashes;
}
