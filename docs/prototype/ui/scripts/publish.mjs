import { cp, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const base = new URL('../', import.meta.url);
// Only replace this prototype's generated UI assets; preserve source and legacy files.
await rm(new URL('../assets/ui', base), {recursive:true, force:true});
await mkdir(new URL('../assets', base), {recursive:true});
await cp(new URL('dist/assets/ui', base), new URL('../assets/ui', base), {recursive:true});
for (const name of ['index','child','parent','platform']) {
  await cp(new URL(`dist/${name}.html`, base), new URL(`../${name}.html`, base));
}
console.log(`Published four entries to ${fileURLToPath(new URL('../', base))}`);
