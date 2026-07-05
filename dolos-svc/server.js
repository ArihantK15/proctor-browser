// Isolated microservice wrapping @dodona/dolos-lib behind one HTTP endpoint.
// Same isolation philosophy as execsvc: the Python API/worker never runs
// Node or Dolos in-process, it calls this over HTTP — a Dolos crash or a
// malformed-input hang can't take down the worker process.
import express from 'express';
import { Dolos } from '@dodona/dolos-lib';
import { writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const LANG_EXT = {
  python: 'py', javascript: 'js', typescript: 'ts',
  c: 'c', cpp: 'cpp', java: 'java',
};

const app = express();
app.use(express.json({ limit: '10mb' }));

app.get('/health', (_req, res) => res.json({ status: 'ok' }));

app.post('/compare', async (req, res) => {
  const { language, submissions } = req.body || {};
  if (!language || !LANG_EXT[language]) {
    return res.status(400).json({ error: `unsupported language: ${language}` });
  }
  if (!Array.isArray(submissions) || submissions.length < 2) {
    return res.json({ pairs: [] });  // nothing to compare
  }

  const ext = LANG_EXT[language];
  const dir = await mkdtemp(join(tmpdir(), 'dolos-'));
  const idByPath = new Map();
  try {
    const files = [];
    for (const sub of submissions) {
      const path = join(dir, `${sub.id}.${ext}`);
      await writeFile(path, sub.source_code ?? '', 'utf8');
      idByPath.set(path, sub.id);
      files.push(path);
    }

    const dolos = new Dolos({ language });
    const report = await dolos.analyzePaths(files);

    const pairs = [];
    for (const pair of report.allPairs()) {
      const idA = idByPath.get(pair.leftFile.path);
      const idB = idByPath.get(pair.rightFile.path);
      if (!idA || !idB) continue;
      pairs.push({
        submission_a_id: idA,
        submission_b_id: idB,
        similarity_score: pair.similarity,
        matched_regions: pair.buildFragments().map(f => ({
          left_start: f.leftSelection.startRow, left_end: f.leftSelection.endRow,
          right_start: f.rightSelection.startRow, right_end: f.rightSelection.endRow,
        })),
      });
    }
    res.json({ pairs });
  } catch (err) {
    console.error('[dolos-svc] compare failed:', err);
    res.status(500).json({ error: String(err) });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

const PORT = process.env.PORT || 8801;
app.listen(PORT, () => console.log(`[dolos-svc] listening on :${PORT}`));
