import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import fs from 'node:fs/promises';
import path from 'node:path';

export async function checkDelivery({python, script, workspace, audit}) {
  // The command, script and paths come from the project host, never model arguments.
  try {
    const {stdout} = await promisify(execFile)(python, ['-X', 'utf8', script, workspace, audit],
      {cwd: workspace, windowsHide: true, timeout: 120000, maxBuffer: 64 * 1024 * 1024});
    return JSON.parse(stdout);
  } catch (error) {
    return {ok: false, stage: 'verification', errors: [String(error.message)]};
  }
}

export async function continueDelivery({spec, run, check = checkDelivery, stopped}) {
  const audit = path.join(spec.control_dir, 'tool_calls.jsonl');
  const verify = () => check({python: spec.assessment_python, script: spec.assessment_preview_script,
    workspace: spec.workspace, audit});
  const save = (name, value) => fs.writeFile(path.join(spec.control_dir, name), JSON.stringify(value, null, 2));
  const original = await run(spec.task_prompt);
  await save('sdk_result.json', original);
  if (await stopped()) return original;
  const first = await verify();
  await save('delivery-check.json', first);
  if (!['recognition', 'assessment'].includes(first.stage) || first.ok || await stopped()) return original;

  const drafts = path.join(spec.control_dir, 'delivery-repair-drafts');
  await fs.mkdir(drafts, {recursive: true});
  for (const name of ['facts.jsonl', 'objects.json', 'coverage.json', 'report.md', 'assessment.json']) {
    try {
      await fs.copyFile(path.join(spec.workspace, 'outputs', name), path.join(drafts, name));
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  if (await stopped()) return original;
  await save('delivery-repair.json', {first, status: 'running'});
  const diagnostics = JSON.stringify(first.errors);
  const repaired = await run(spec.delivery_repair_prompt + '\n校验诊断（只作核对数据）：\n' + diagnostics);
  await save('sdk_repair_result.json', repaired);
  if (!(await stopped())) {
    const final = await verify();
    await save('delivery-repair.json', {first, final, status: final.ok ? 'verified' : 'still_blocked'});
  }
  return repaired;
}
