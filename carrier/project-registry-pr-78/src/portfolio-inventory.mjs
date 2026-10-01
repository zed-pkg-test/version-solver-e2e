const ROLE_ALIASES = [
  ['.github', 'org-policy'],
  ['-admin-web-server.rs', 'admin-web-server'],
  ['-admin-api-server.rs', 'admin-api-server'],
  ['-desktop-app.rs', 'desktop-app'],
  ['-desktop.rs', 'desktop-app'],
  ['-mcp-server.rs', 'mcp-server'],
  ['-web-server.rs', 'web-server'],
  ['-api-server.rs', 'api-server'],
  ['-pub-lib-core', 'pub-lib-core'],
  ['-orm-core', 'orm-core'],
  ['-lib-core', 'lib-core'],
  ['-interfaces', 'interfaces'],
  ['-clients', 'clients'],
  ['-monorepo', 'monorepo'],
  ['-infra', 'infra'],
  ['-worker.rs', 'worker'],
  ['-sidecar.rs', 'sidecar'],
  ['-operator.rs', 'operator'],
  ['-gateway.rs', 'gateway'],
  ['-daemon.rs', 'daemon'],
  ['-agent.rs', 'agent'],
  ['-sync', 'sync'],
  ['-docs', 'docs'],
  ['-e2e', 'e2e'],
  ['-cli.rs', 'cli'],
  ['-cli', 'cli'],
  ['-lib', 'lib']
].sort((a, b) => b[0].length - a[0].length);

export function classifyRepositoryRole(repository) {
  const name = repository.name;
  if (name === '.github') {
    return 'org-policy';
  }
  if (name.endsWith('.github.io')) {
    return 'site';
  }
  for (const [suffix, role] of ROLE_ALIASES) {
    if (name.endsWith(suffix)) {
      return role;
    }
  }
  return 'unclassified';
}

function sortByFullName(a, b) {
  return a.fullName.localeCompare(b.fullName, 'en', { sensitivity: 'base' });
}

export function normalizeRepository(repository) {
  const archived = Boolean(repository.archived);
  return {
    repositoryId: Number(repository.repositoryId ?? repository.id),
    fullName: repository.fullName ?? repository.repository_full_name,
    owner: repository.owner?.login ?? repository.owner,
    name: repository.name,
    visibility: repository.visibility ?? 'public',
    archived,
    defaultBranch: repository.defaultBranch ?? repository.default_branch ?? null,
    headSha: repository.headSha ?? null,
    inspectionStatus: archived ? 'archived' : (repository.inspectionStatus ?? 'uninspected'),
    role: repository.role ?? classifyRepositoryRole(repository),
    contractAuthorities: repository.contractAuthorities ?? [],
    dependencies: repository.dependencies ?? [],
    release: repository.release ?? { mechanism: 'unknown' },
    languages: [...(repository.languages ?? [])].sort(),
    testOrganizations: [...(repository.testOrganizations ?? [])].sort(),
    deploymentConsumers: [...(repository.deploymentConsumers ?? [])].sort(),
    prDependencies: [...(repository.prDependencies ?? [])].sort((a, b) =>
      `${a.repository}#${a.prNumber}`.localeCompare(`${b.repository}#${b.prNumber}`)
    )
  };
}

export function buildPortfolioInventory({
  projectContextsSha,
  owners,
  repositories,
  generatedAt = new Date().toISOString(),
  collectionMode = 'github-app-installations'
}) {
  const normalizedRepositories = repositories.map(normalizeRepository).sort(sortByFullName);
  const seenIds = new Set();
  const seenNames = new Set();
  for (const repository of normalizedRepositories) {
    if (!Number.isInteger(repository.repositoryId) || repository.repositoryId < 1) {
      throw new Error(`invalid repository id for ${repository.fullName}`);
    }
    const foldedName = repository.fullName.toLowerCase();
    if (seenIds.has(repository.repositoryId)) {
      throw new Error(`duplicate repository id ${repository.repositoryId}`);
    }
    if (seenNames.has(foldedName)) {
      throw new Error(`duplicate repository name ${repository.fullName}`);
    }
    seenIds.add(repository.repositoryId);
    seenNames.add(foldedName);
  }

  const normalizedOwners = [...owners]
    .map((owner) => ({ ...owner, ownerId: Number(owner.ownerId) }))
    .sort((a, b) => a.account.localeCompare(b.account, 'en', { sensitivity: 'base' }));
  const seenOwners = new Set();
  for (const owner of normalizedOwners) {
    const folded = owner.account.toLowerCase();
    if (seenOwners.has(folded)) {
      throw new Error(`duplicate owner ${owner.account}`);
    }
    seenOwners.add(folded);
  }

  return {
    schemaVersion: 1,
    authority: 'ORESoftware/project-registry',
    generatedAt,
    source: {
      projectContextsSha,
      collectionMode,
      owners: normalizedOwners
    },
    repositories: normalizedRepositories
  };
}

export function summarizeInventory(inventory) {
  const summary = {
    owners: inventory.source.owners.length,
    ownersInspected: 0,
    ownersPartial: 0,
    ownersInaccessible: 0,
    ownersNotInstalled: 0,
    repositories: inventory.repositories.length,
    inspected: 0,
    uninspected: 0,
    inaccessible: 0,
    archived: 0,
    unclassified: 0
  };
  for (const owner of inventory.source.owners) {
    if (owner.status === 'inspected') summary.ownersInspected += 1;
    if (owner.status === 'partial') summary.ownersPartial += 1;
    if (owner.status === 'inaccessible') summary.ownersInaccessible += 1;
    if (owner.status === 'not-installed') summary.ownersNotInstalled += 1;
  }
  for (const repository of inventory.repositories) {
    summary[repository.inspectionStatus] += 1;
    if (repository.role === 'unclassified') {
      summary.unclassified += 1;
    }
  }
  return summary;
}
