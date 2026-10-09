import { readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
const root = fileURLToPath(new URL('../', import.meta.url))
const violations = []
async function scan(dir) {
  for (const entry of await readdir(dir, {withFileTypes:true})) {
    if (['node_modules','dist','playwright-report','test-results'].includes(entry.name)) continue
    const file=path.join(dir,entry.name)
    if (entry.isDirectory()) await scan(file)
    else if (/\.tsx?$/.test(file) || (file.endsWith('.vue') && /<script[^>]*lang\s*=\s*["']tsx?["']/.test(await readFile(file,'utf8')))) violations.push(path.relative(root,file))
  }
}
for(const dir of ['frontend-user','frontend-admin']) await scan(path.join(root,dir))
if(violations.length){console.error('TypeScript sources remain:\n'+violations.join('\n'));process.exitCode=1}
else console.log('Both frontends contain only JavaScript / Vue SFC scripts; no TypeScript sources.')
