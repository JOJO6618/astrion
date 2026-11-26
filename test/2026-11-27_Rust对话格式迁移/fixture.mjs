import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export async function migrate(base, headers, expectedFailed = 0) {
  const path = base + '/api/conversation-storage/migration';
  const start = await fetch(path, {method: 'POST', headers});
  assert.equal(start.status, 200, await start.text());
  for (let i=0; i<200; i++) {
    const status=await fetch(path, {headers});
    assert.equal(status.status,200);
    const report=await status.json();
    if (!report.running) { assert.equal(report.failed,expectedFailed,JSON.stringify(report)); return report; }
    await new Promise(resolve=>setTimeout(resolve,25));
  }
  throw new Error('Migration did not finish');
}

export function withDb(directory, callback, readOnly = true) {
  const db = new DatabaseSync(join(directory,'conversations.sqlite3'),{readOnly});
  try {db.exec('PRAGMA busy_timeout=5000'); return callback(db);}
  finally {db.close();}
}

export function readDocument(directory,id) {
  return withDb(directory,db=>{
    if (!db.prepare('SELECT 1 FROM conversations WHERE id=?').get(id)) return null;
    const result={};
    for(const {scope,key,value} of db.prepare("SELECT scope,key,value FROM fields WHERE conversation=? ORDER BY (scope='root') DESC").all(id)) {
      const target=scope==='root'?result:result[scope];
      target[key]=JSON.parse(value);
    }
    result.messages=db.prepare('SELECT body FROM messages WHERE conversation=? ORDER BY seq').all(id).map(r=>JSON.parse(r.body));
    return result;
  });
}

export function readSummary(directory,id) {
  return withDb(directory,db=>{
    const row=db.prepare('SELECT summary FROM conversations WHERE id=?').get(id);
    return row?JSON.parse(row.summary):null;
  });
}

// Some headless regressions need a prepared on-disk conversation before the first HTTP call.
export function seedDocument(directory, doc) {
  mkdirSync(directory,{recursive:true});
  withDb(directory, db=>{
    db.exec('PRAGMA foreign_keys=ON; BEGIN IMMEDIATE;');
    if(!db.prepare("SELECT 1 FROM sqlite_master WHERE name='conversations'").get()){
      for(const file of ['schema.sql','media_indexes.sql']){
        db.exec(readFileSync(new URL('../../backend-rust/src/conversation_db/'+file,import.meta.url),'utf8'));
      }
    }
    const id=doc.id;
    db.prepare('DELETE FROM conversations WHERE id=?').run(id);
    const summary={...doc.metadata,title:doc.title,created_at:doc.created_at,updated_at:doc.updated_at,total_messages:doc.messages.length};
    db.prepare('INSERT INTO conversations(id,summary,message_count,tool_count) VALUES(?,?,?,?)').run(id,JSON.stringify(summary),doc.messages.length,doc.messages.reduce((n,m)=>n+(m.tool_calls?.length??0),0));
    const field=db.prepare('INSERT INTO fields(conversation,scope,key,value) VALUES(?,?,?,?)');
    for(const [key,value] of Object.entries(doc)){
      if(key==='messages') continue;
      if(['metadata','token_statistics'].includes(key) && value && typeof value==='object' && !Array.isArray(value)){
        field.run(id,'root',key,'{}');
        for(const [k,v] of Object.entries(value)) field.run(id,key,k,JSON.stringify(v));
      } else { field.run(id,'root',key,JSON.stringify(value)); }
    }
    const message=db.prepare('INSERT INTO messages(conversation,seq,message_id,role,body) VALUES(?,?,?,?,?)');
    doc.messages.forEach((m,i)=>message.run(id,i,m.message_id??null,m.role,JSON.stringify(m)));
    db.exec('COMMIT');
  },false);
}
