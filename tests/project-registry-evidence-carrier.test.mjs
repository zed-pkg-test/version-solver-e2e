import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  buildPortfolioInventory,
  classifyRepositoryRole,
  summarizeInventory
} from '../carrier/project-registry-pr-78/src/portfolio-inventory.mjs';
import {
  assertUsableCertification,
  receiptMatchesExactState,
  validateCertificationSemantics
} from '../carrier/project-registry-pr-78/src/certification-evidence.mjs';

const ROOT = 'carrier/project-registry-pr-78';
const manifest = JSON.parse(await readFile(`${ROOT}/source-manifest.json`, 'utf8'));
const SHA_A = 'a'.repeat(40);
const SHA_B = 'b'.repeat(40);

function gitBlobSha(bytes) {
  return createHash('sha1')
    .update(`blob ${bytes.length}\0`)
    .update(bytes)
    .digest('hex');
}

test('carrier is pinned to the exact private-source head', () => {
  assert.equal(manifest.source_repository, 'ORESoftware/project-registry');
  assert.equal(manifest.source_pull_request, 78);
  assert.equal(manifest.source_head, '11559ec51706bd80d76e38c7ee942644bf9f8669');
  assert.match(manifest.certification_scope, /supplemental/);
});

test('every mirrored source file is byte-identical by Git blob id', async () => {
  for (const [sourcePath, expectedBlob] of Object.entries(manifest.source_files)) {
    const bytes = await readFile(`${ROOT}/${sourcePath}`);
    assert.equal(gitBlobSha(bytes), expectedBlob, `${sourcePath} drifted from source PR`);
  }
});

test('inventory retains incomplete acquisition states instead of hiding them', () => {
  assert.equal(classifyRepositoryRole({ name: 'svc-api-server.rs' }), 'api-server');
  const inventory = buildPortfolioInventory({
    projectContextsSha: SHA_A,
    collectionMode: 'fixture',
    generatedAt: '2026-09-25T00:00:00.000Z',
    owners: [
      { account: 'alpha', ownerId: 1, status: 'inspected' },
      { account: 'beta', ownerId: 2, status: 'partial' },
      { account: 'gamma', ownerId: 3, status: 'not-installed' },
      { account: 'delta', ownerId: 4, status: 'inaccessible', error: 'fixture' }
    ],
    repositories: [{
      id: 10,
      repository_full_name: 'alpha/svc-api-server.rs',
      owner: { login: 'alpha' },
      name: 'svc-api-server.rs',
      visibility: 'private',
      archived: false,
      default_branch: 'main'
    }]
  });
  const summary = summarizeInventory(inventory);
  assert.equal(summary.owners, 4);
  assert.equal(summary.ownersPartial, 1);
  assert.equal(summary.ownersNotInstalled, 1);
  assert.equal(summary.ownersInaccessible, 1);
  assert.equal(summary.uninspected, 1);
});

test('certification rejects zero-step PASS and stale source state', () => {
  const receipt = {
    source: { repository: 'ORESoftware/project-registry', commit: SHA_A, dependencies: [] },
    carrier: { repository: 'zed-pkg-test/version-solver-e2e', kind: 'alternate-organization' },
    result: { status: 'PASS', failureClass: 'none' },
    runner: { runId: 1, runnerId: 0, jobStarted: false, stepsExecuted: 0 }
  };
  assert.deepEqual(validateCertificationSemantics(receipt), [
    'PASS requires a started job with at least one executed step',
    'a job that never started must be classified as runner-acquisition'
  ]);

  receipt.runner = { runId: 1, runnerId: 42, jobStarted: true, stepsExecuted: 3 };
  assert.equal(receiptMatchesExactState(receipt, { sourceCommit: SHA_A }), true);
  assert.equal(receiptMatchesExactState(receipt, { sourceCommit: SHA_B }), false);
  assert.equal(assertUsableCertification(receipt, { sourceCommit: SHA_A }), true);
  assert.throws(() => assertUsableCertification(receipt, { sourceCommit: SHA_B }), /stale/);
});

test('schemas encode the required fail-closed evidence states', async () => {
  const inventorySchema = JSON.parse(await readFile(`${ROOT}/schema/portfolio-repository-inventory.schema.json`, 'utf8'));
  const certificationSchema = JSON.parse(await readFile(`${ROOT}/schema/certification-evidence.schema.json`, 'utf8'));
  const formalSchema = JSON.parse(await readFile(`${ROOT}/schema/formal-evidence.schema.json`, 'utf8'));

  assert.deepEqual(
    inventorySchema.properties.source.properties.owners.items.properties.status.enum,
    ['inspected', 'partial', 'inaccessible', 'not-installed']
  );
  assert.deepEqual(
    certificationSchema.properties.result.properties.status.enum,
    ['PASS', 'FAIL', 'NOT_RUN', 'BLOCKED']
  );
  assert.equal(
    formalSchema.$defs.claim.properties.evidenceClasses.contains.const,
    'implementation-linked-tests'
  );
  assert.equal(
    formalSchema.$defs.claim.properties.negativeControl.properties.detected.const,
    true
  );
});
