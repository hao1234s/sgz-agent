"""Run the smoke script's actual gate with offline tool fixtures, never DSH."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'Test-Dsh.cjs'
HARNESS = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(process.argv[1]);
const moduleFixture = {exports: {}};
const deny = () => {throw Error('offline_fixture_forbids_boot_or_filesystem');};
const sandboxRequire = name => {
  if (name === 'node:fs') return new Proxy({}, {get: () => deny});
  if (name === 'node:module') return {createRequire: deny};
  return require(name);
};
vm.runInNewContext(fs.readFileSync(input.script, 'utf8'), {
  require: sandboxRequire, module: moduleFixture,
  __dirname: require('node:path').dirname(input.script),
  process: {argv: [], env: {}, execPath: 'offline-node'}, console,
  setTimeout: callback => callback(),
});
const gate = moduleFixture.exports.verifyCapabilityContracts;
if (typeof gate !== 'function') throw Error('missing_offline_capability_gate');
const calls = [];
const reason = 'native_quiescence_unconfirmed';
const status = {world: 'fixture-world', capabilities: {
  replenish: {available: input.ready === true, reason: input.ready ? null : reason,
    ...(input.readAvailable === undefined ? {} : {
      read_available: input.readAvailable,
      read_reason: input.readAvailable ? null : 'read_runtime_quarantined:fixture',
    })}
}};
let huntReads = 0;
const tools = new Map();
for (const name of ['protocol_status', 'scan_region', 'search_map', 'game_status', 'replenish', 'hunt_status']) {
  if (input.missingTool === name) continue;
  tools.set('sgz_' + name, {execute: async args => {
    calls.push({name, args});
    if (name === 'protocol_status') return {
      api_scan_available: input.ready === true,
      world_bandits_complete: false,
      reason: input.ready ? null : 'api_runtime_quarantined:fixture',
    };
    if (name === 'hunt_status') {
      huntReads++;
      return {capabilities: {
        available: input.ready === true,
        missing: input.ready ? [] : ['hunt_runtime_quarantined:fixture'],
        mutations_enabled: input.ready === true,
      }, campaigns: input.fabricateCampaign && huntReads > 1 ? [{id: 'invented'}] : []};
    }
    if (name === 'replenish') {
      if (args.dry_run !== true) throw Error('mutation_forbidden');
      if (input.readAvailable) return {dry_run: true, armies: [{army_id: '1', sent: false, status: 'ready'}]};
      if (input.refillSucceeds) return {status: 'complete'};
      throw Error(input.wrongReason ? 'unrelated_failure' :
        status.capabilities.replenish.read_reason || reason);
    }
    if (!input.ready) throw Error('quarantined_game_access_forbidden:' + name);
    if (name === 'scan_region') return {id: 'fixture-scan'};
    if (name === 'game_status') return {jobs: [{id: 'fixture-scan', status: 'complete', reason: 'fixture'}]};
    if (name === 'search_map') return {results: [{source: 'client_api', level: 4}]};
    throw Error('unexpected_tool:' + name);
  }});
}
(async () => {
  try {
    const report = await gate({tools}, status);
    console.log(JSON.stringify({report, calls}));
  } catch (error) {
    console.log(JSON.stringify({error: error.message, calls}));
  }
})();
'''


def run_gate(**options):
    node = shutil.which('node')
    assert node, 'Node is required for the DSH smoke contract tests'
    result = subprocess.run(
        [node, '-e', HARNESS, json.dumps({'script': str(SCRIPT), **options})],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_quarantine_checks_disabled_contract_without_reporting_live_success():
    result = run_gate()
    assert 'error' not in result, result
    report = result['report']
    assert report['api_scan']['live_execution'] == 'not_verified'
    assert report['api_scan']['reason'] == 'api_runtime_quarantined:fixture'
    assert report['replenish']['disabled_path'] == 'passed'
    assert report['replenish']['live_execution'] == 'not_verified'
    assert report['hunt']['status_check'] == 'passed'
    assert report['hunt']['live_execution'] == 'not_verified'
    assert report['hunt']['missing'] == ['hunt_runtime_quarantined:fixture']
    assert [c['name'] for c in result['calls']] == [
        'protocol_status', 'hunt_status', 'replenish', 'hunt_status',
    ]
    assert result['calls'][2]['args']['dry_run'] is True


@pytest.mark.parametrize('options', [
    {'refillSucceeds': True}, {'wrongReason': True},
    {'missingTool': 'replenish'}, {'fabricateCampaign': True},
])
def test_disabled_contract_rejects_false_success_and_side_effects(options):
    assert 'error' in run_gate(**options)


def test_ready_scan_remains_verified_but_never_sends_gameplay_commands():
    result = run_gate(ready=True)
    assert 'error' not in result, result
    assert result['report']['api_scan']['live_execution'] == 'passed'
    assert result['report']['replenish']['live_execution'] == 'not_verified'
    assert result['report']['hunt']['live_execution'] == 'not_verified'
    assert not any(c['name'] == 'replenish' or c['name'].startswith('hunt_')
                   and c['name'] != 'hunt_status' for c in result['calls'])


def test_write_quarantine_allows_verified_read_preview_without_claiming_refill():
    result = run_gate(readAvailable=True)
    assert 'error' not in result, result
    assert result['report']['replenish']['write_disabled'] is True
    assert result['report']['replenish']['read_reason'] is None
    assert result['report']['replenish']['preview_check'] == 'passed'
    assert result['report']['replenish']['disabled_path'] == 'not_checked'
    assert result['report']['replenish']['live_execution'] == 'not_verified'
    refill = [c for c in result['calls'] if c['name'] == 'replenish']
    assert len(refill) == 1
    assert refill[0]['args']['dry_run'] is True


def test_disabled_preview_uses_read_reason_instead_of_write_reason():
    result = run_gate(readAvailable=False)
    assert 'error' not in result, result
    assert result['report']['replenish']['read_reason'] == 'read_runtime_quarantined:fixture'
    assert result['report']['replenish']['disabled_path'] == 'passed'
