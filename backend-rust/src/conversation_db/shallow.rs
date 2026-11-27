use super::{Database, error, load_header, sync_header};
use crate::runtime::shallow::Settings;
use rusqlite::{TransactionBehavior, params};
use serde_json::{Value, json};

impl Database {
    pub(crate) fn shallow_compress(
        &mut self,
        id: &str,
        settings: &Settings,
        refresh: impl FnOnce(&mut Value) -> Result<(), String>,
        stamp: &str,
    ) -> Result<Vec<String>, String> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(error)?;
        let mut header = load_header(&tx, id).map_err(error)?;
        let tokens = header["token_statistics"]["current_context_tokens"]
            .as_u64()
            .unwrap_or(0);
        if header["metadata"]["is_long_conversation"] != true
            && tokens <= settings.threshold
        {
            return Ok(Vec::new());
        }
        let count: u64 = tx
            .query_row(
                "SELECT count(*) FROM messages WHERE conversation=? AND role='tool'",
                [id],
                |r| r.get(0),
            )
            .map_err(error)?;
        let previous = header["metadata"]["last_shallow_compress_tool_count"]
            .as_u64()
            .unwrap_or(0);
        if previous != 0 && count.saturating_sub(previous) < settings.interval {
            return Ok(Vec::new());
        }
        let rows = {
            let mut query=tx.prepare("
                WITH protected_turns AS (
                  SELECT u.seq AS start,
                         coalesce((SELECT min(n.seq) FROM messages n WHERE n.conversation=?1 AND n.role='user' AND n.seq>u.seq),9223372036854775807) AS stop
                  FROM messages u WHERE u.conversation=?1 AND role='user' AND NOT coalesce(json_extract(body,'$.metadata.is_auto_generated'),0)
                    AND coalesce(json_extract(body,'$.content'),'') NOT LIKE '这是一句系统自动发送的user消息，用于通知你%'
                  ORDER BY seq DESC LIMIT ?2
                )
                SELECT seq,json_extract(body,'$.tool_call_id') FROM messages m
                WHERE conversation=?1 AND role='tool'
                  AND NOT coalesce(json_extract(body,'$.metadata.auto_shallow_compacted'),0)
                  AND NOT coalesce(json_extract(body,'$.metadata.deep_compacted'),0)
                  AND json_extract(body,'$.name') IN ('write_file','read_file','edit_file','terminal_input','terminal_snapshot','web_search','extract_webpage','run_command','view_image','view_video')
                  AND json_type(body,'$.tool_call_id')='text'
                  AND seq NOT IN (SELECT seq FROM messages WHERE conversation=?1 AND role='tool' ORDER BY seq DESC LIMIT ?3)
                  AND NOT EXISTS(SELECT 1 FROM protected_turns WHERE m.seq>start AND m.seq<stop)
                ORDER BY seq LIMIT ?4").map_err(error)?;
            query
                .query_map(
                    params![
                        id,
                        settings.user_turns,
                        settings.recent_tools,
                        settings.batch
                    ],
                    |r| Ok((r.get::<_, usize>(0)?, r.get::<_, String>(1)?)),
                )
                .map_err(error)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(error)?
        };
        let meta = &mut header["metadata"];
        meta["is_long_conversation"] = json!(true);
        meta["tool_call_count"] = json!(count);
        meta["last_shallow_compress_tool_count"] = json!(count);
        if !rows.is_empty() {
            let round = meta["shallow_compression_round"].as_u64().unwrap_or(0) + 1;
            meta["shallow_compression_round"] = json!(round);
            for (seq, _) in &rows {
                tx.execute("UPDATE messages SET body=json_set(body,'$.metadata',json_set(CASE WHEN json_type(body,'$.metadata')='object' THEN json_extract(body,'$.metadata') ELSE '{}' END,'$.auto_shallow_compacted',json('true'),'$.auto_shallow_compacted_at',?3,'$.auto_shallow_compact_round',?4)) WHERE conversation=?1 AND seq=?2",params![id,seq,stamp,round]).map_err(error)?;
            }
            refresh(&mut header)?;
        }
        sync_header(&tx, id, &header).map_err(error)?;
        tx.commit().map_err(error)?;
        Ok(rows.into_iter().map(|(_, call)| call).collect())
    }
}
