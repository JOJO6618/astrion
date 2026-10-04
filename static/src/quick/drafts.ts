let database: Promise<IDBDatabase> | undefined;
function openDatabase() {
  database ||= new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('astrion-quick-entry', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}
export async function saveDraft(key: string, value: any) {
  // Node regression harnesses have no IndexedDB. Browser screenshots use the
  // larger structured store rather than exceeding localStorage's small quota.
  if (typeof indexedDB === 'undefined') {
    localStorage.setItem(key, JSON.stringify(value));
    return;
  }
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite');
    transaction.objectStore('drafts').put(JSON.parse(JSON.stringify(value)), key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
export async function loadDraft(key: string) {
  if (typeof indexedDB === 'undefined') return JSON.parse(localStorage.getItem(key) || '{}');
  const db = await openDatabase();
  return await new Promise<any>((resolve, reject) => {
    const request = db.transaction('drafts').objectStore('drafts').get(key);
    request.onsuccess = () => resolve(request.result || {});
    request.onerror = () => reject(request.error);
  });
}
