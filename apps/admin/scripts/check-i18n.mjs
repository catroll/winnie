// Fail the build when a static tr('...') key is missing from zh-CN.
// Dynamic keys (tr(variable)) are intentionally skipped.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = path.join(root, 'src');
const locales = JSON.parse(
  fs.readFileSync(path.join(src, 'i18n/locales/zh-CN.json'), 'utf8'),
);
const keys = new Set();

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full);
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      const text = fs.readFileSync(full, 'utf8');
      for (const match of text.matchAll(/\btr\(\s*(['"])((?:\\.|(?!\1).)*)\1/g)) {
        keys.add(
          match[2]
            .replace(/\\n/g, '\n')
            .replace(/\\r/g, '\r')
            .replace(/\\t/g, '\t')
            .replace(/\\(['"\\])/g, '$1'),
        );
      }
    }
  }
}

walk(src);
const missing = [...keys].filter((key) => !(key in locales)).sort();
if (missing.length > 0) {
  console.error(`Missing ${missing.length} zh-CN keys:`);
  for (const key of missing) {
    console.error(`  ${JSON.stringify(key)}`);
  }
  process.exit(1);
}
console.log(`i18n: ${keys.size} static keys present in zh-CN`);
