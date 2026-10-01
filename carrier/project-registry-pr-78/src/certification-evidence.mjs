function dependencyMap(dependencies) {
  return new Map(dependencies.map((dependency) => [dependency.repository.toLowerCase(), dependency.commit]));
}

export function validateCertificationSemantics(receipt) {
  const errors = [];
  const { result, runner, carrier, source } = receipt;

  if (result.status === 'PASS' && (!runner.jobStarted || runner.stepsExecuted < 1)) {
    errors.push('PASS requires a started job with at least one executed step');
  }
  if (!runner.jobStarted && runner.stepsExecuted !== 0) {
    errors.push('a job that never started cannot have executed steps');
  }
  if (!runner.jobStarted && result.failureClass !== 'runner-acquisition') {
    errors.push('a job that never started must be classified as runner-acquisition');
  }
  if (result.status === 'NOT_RUN' && runner.jobStarted && runner.stepsExecuted > 0) {
    errors.push('NOT_RUN cannot describe a job that executed steps');
  }
  if (carrier.kind === 'source-repository' && carrier.repository.toLowerCase() !== source.repository.toLowerCase()) {
    errors.push('source-repository carrier must equal the source repository');
  }
  if (carrier.kind === 'alternate-organization') {
    const sourceOwner = source.repository.split('/')[0].toLowerCase();
    const carrierOwner = carrier.repository.split('/')[0].toLowerCase();
    if (sourceOwner === carrierOwner) {
      errors.push('alternate-organization carrier must use a different GitHub owner');
    }
  }

  return errors;
}

export function receiptMatchesExactState(receipt, { sourceCommit, dependencyCommits = [] }) {
  if (receipt.source.commit !== sourceCommit) {
    return false;
  }
  const expected = dependencyMap(dependencyCommits);
  const recorded = dependencyMap(receipt.source.dependencies);
  if (expected.size !== recorded.size) {
    return false;
  }
  for (const [repository, commit] of expected) {
    if (recorded.get(repository) !== commit) {
      return false;
    }
  }
  return true;
}

export function assertUsableCertification(receipt, exactState) {
  const errors = validateCertificationSemantics(receipt);
  if (errors.length) {
    throw new Error(`invalid certification receipt: ${errors.join('; ')}`);
  }
  if (receipt.result.status !== 'PASS') {
    throw new Error(`certification is ${receipt.result.status}, not PASS`);
  }
  if (!receiptMatchesExactState(receipt, exactState)) {
    throw new Error('certification is stale for the requested source/dependency state');
  }
  return true;
}
