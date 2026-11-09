export async function runMigration(credentials, check, phase) {
  const csv = '\ufeffname,url,username,password,note\r\n"合成,名称",https://csv.example.test/login,"user,中文","fake,""quoted""\nsecond line",note\r\nsecond,https://csv2.example.test/,u,other-fake-password,\r\n';
  let preview = (await credentials('preview-file', { format: 'password-csv', content: csv })).preview;
  check('filePreviewMasksSecrets', !JSON.stringify(preview).includes('other-fake-password') && !JSON.stringify(preview).includes('quoted'));
  let report = (await credentials('import', { id: preview.id, choices: {}, replaceCookies: false })).report;
  check(phase === 'write' ? 'csvAtomicVaultImport' : 'csvDuplicatesAfterRestart', report.stats.passwords.imported === (phase === 'write' ? 2 : 0));
  const rows = (await credentials('list')).records;
  const row = rows.find(row => row.origin === 'https://csv.example.test');
  check('csvMetadataAvailable', row?.username === 'user,中文' && !Object.hasOwn(row, 'password'));
  check('csvQuotedPasswordPreserved', (await credentials('reveal', undefined, row.id)).password === 'fake,"quoted"\nsecond line');
  const cookies = [
    { name: 'same', domain: 'migration.example.test', path: '/', value: 'synthetic-unpartitioned', secure: true, httpOnly: true, hostOnly: true, session: false, expirationDate: 4102444800, sameSite: 'no_restriction' },
    { name: 'same', domain: 'migration.example.test', path: '/', value: 'synthetic-top-one', secure: true, httpOnly: true, hostOnly: true, session: false, expirationDate: 4102444800, sameSite: 'no_restriction', partitionKey: { topLevelSite: 'https://one.test', hasCrossSiteAncestor: true } },
    { name: 'same', domain: 'migration.example.test', path: '/', value: 'synthetic-top-two', secure: true, httpOnly: false, hostOnly: true, session: false, expirationDate: 4102444800, sameSite: 'strict', partitionKey: { topLevelSite: 'https://two.test', hasCrossSiteAncestor: false } },
    { name: 'domain-cookie', domain: '.migration.example.test', path: '/scope', value: 'synthetic-domain', secure: true, httpOnly: false, hostOnly: false, session: false, expirationDate: 4102444800, sameSite: 'unspecified' }
  ];
  const content = JSON.stringify({ format: 'astrion-cookie-export', version: 1, cookies });
  preview = (await credentials('preview-file', { format: 'cookie-json', content })).preview;
  check('partitionCookieFilePreview', preview.stats.cookies.ready === 4 && !JSON.stringify(preview).includes('synthetic-top-one'));
  report = (await credentials('import', { id: preview.id, choices: {}, replaceCookies: false })).report;
  check(phase === 'write' ? 'partitionCookiesWrittenAndReadBack' : 'partitionCookiesPersistedWithDistinctKeys', report.stats.cookies.imported === (phase === 'write' ? 4 : 0) && report.stats.cookies.failed === 0 && (phase !== 'read' || report.stats.cookies.reasons.duplicate === 4));
  // Replacement must target only the matching domain/name/path/partition key.
  if (phase === 'write') {
    cookies[1].value = 'synthetic-top-one-replaced';
    preview = (await credentials('preview-file', { format: 'cookie-json', content: JSON.stringify({ format: 'astrion-cookie-export', version: 1, cookies: [cookies[1]] }) })).preview;
    report = (await credentials('import', { id: preview.id, choices: {}, replaceCookies: true })).report;
    check('partitionReplacementReadBack', report.stats.cookies.imported === 1 && report.stats.cookies.failed === 0);
  }
  preview = (await credentials('preview-file', { format: 'password-csv', content: csv })).preview;
  await credentials('cancel-import');
  let refused = false;
  try { await credentials('import', { id: preview.id, choices: {}, replaceCookies: false }); } catch { refused = true; }
  check('filePreviewCancelRejectsCommit', refused);
}
