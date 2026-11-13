import { randomUUID } from 'node:crypto';
import { cookieIdentity } from './cookie-store.js';

export function importedCookie(row) {
  const domain = row.domain.replace(/^\./, '').toLowerCase();
  const normalizedDomain = row.domain.startsWith('.') ? `.${domain}` : domain;
  const url = new URL(`${row.secure ? 'https' : 'http'}://${domain}${row.path || '/'}`);
  if (url.hostname !== domain || url.username || url.password) throw new Error('invalid_record');
  const cookie = { url: url.href, name: row.name, value: row.value, path: row.path,
    secure: row.secure, httpOnly: row.httpOnly };
  // Omitting domain is essential for a host-only cookie.
  if (row.domain.startsWith('.')) cookie.domain = normalizedDomain;
  if (row.expires > 0) cookie.expires = row.expires;
  const sameSite = { 0: 'None', 1: 'Lax', 2: 'Strict' }[row.sameSite];
  if (sameSite) cookie.sameSite = sameSite;
  if (row.partitionKey) cookie.partitionKey = { ...row.partitionKey };
  return { cookie, identity: cookieIdentity({ ...cookie, domain: normalizedDomain }) };
}

/** Shared destination-side grouping: secrets stay in the expiring main-process job. */
export function rememberImportPreview(importer, profile, vault, rows, selected, ensure) {
  ensure();
  const { stats, passwords: records, cookies, history } = rows;
  const groups = new Map(), conflicts = [];
  for (const row of records) {
    const identity = JSON.stringify([row.origin, row.username]);
    const current = vault.find(row.origin, row.username);
    let group = groups.get(identity);
    if (!group) {
      group = { id: randomUUID(), origin: row.origin, username: row.username,
        existingId: current?.id || '', candidates: [] };
      groups.set(identity, group);
    }
    if (current?.password === row.password || group.candidates.some(item => item.password === row.password)) {
      stats.passwords.ready--; stats.passwords.skipped++;
      stats.passwords.reasons.duplicate = (stats.passwords.reasons.duplicate || 0) + 1;
      continue;
    }
    group.candidates.push({ ...row, id: randomUUID() });
  }
  const passwords = [...groups.values()].filter(group => group.candidates.length);
  for (const group of passwords) {
    if (group.existingId || group.candidates.length > 1) {
      stats.passwords.conflicts++;
      conflicts.push({ id: group.id, origin: group.origin, username: group.username,
        hasExisting: !!group.existingId,
        candidates: group.candidates.map(({ id, sourceURL, modified }) => ({ id, sourceURL, modified })) });
    }
  }
  ensure();
  importer.cancelProfile(profile);
  const id = randomUUID(), timer = setTimeout(() => importer.cancel(id), 300000);
  timer.unref();
  importer.jobs.set(id, { id, profile, stats, passwords, cookies, history, conflicts, timer,
    vaultState: selected.passwords ? JSON.stringify(vault.list()) : null });
  return { id, stats, conflicts };
}
