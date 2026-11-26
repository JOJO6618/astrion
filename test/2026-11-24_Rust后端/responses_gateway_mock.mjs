import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { closeServer, listenOnLoopback } from './gateway_harness.mjs';

const PATH = '/v1/responses';
const CALL_ITEM_ID = 'fc_responses_gateway_server_item';

export const RESPONSES = Object.freeze({
  model: 'gateway-offline-responses-model',
  apiKey: 'fixture-responses-original-key',
  rotatedApiKey: 'fixture-responses-rotated-key',
  toolName: 'astrion_echo',
  callId: 'call_responses_gateway_echo',
  echo: '回声 "Responses" \\ 续轮',
  firstPrompt: '用回声工具验证 Responses 续轮。',
  answerStart: '工具已经完成。',
  answerEnd: 'Responses 续轮成功。',
  nextPrompt: '请继续这个会话。',
  nextAnswer: '同一进程的会话已续接。',
  restartPrompt: '重启后请继续同一个会话。',
  restartAnswer: '重启后已续接。',
  rotatedPrompt: '更换凭证后请继续会话。',
  rotatedAnswer: '换凭证后已续接。',
  reasoningSummary: '核对回声工具参数',
});

export const TOOL_REASONING = Object.freeze({
  type: 'reasoning',
  id: 'rs_responses_gateway_tool',
  summary: [{ type: 'summary_text', text: RESPONSES.reasoningSummary }],
  encrypted_content: 'opaque-responses-gateway-tool-transport-fixture',
});

export const ANSWER_REASONING = Object.freeze({
  type: 'reasoning',
  id: 'rs_responses_gateway_answer',
  summary: [],
  encrypted_content: 'opaque-responses-gateway-answer-transport-fixture',
});

function writeEvent(response, type, data = {}) {
  response.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`);
}

function complete(response, output = []) {
  writeEvent(response, 'response.completed', { response: { output } });
  response.end();
}

function text(response, delta) {
  writeEvent(response, 'response.output_text.delta', { delta });
}

function toolTurn(response) {
  writeEvent(response, 'response.reasoning_summary_text.done', {
    item_id: TOOL_REASONING.id, summary_index: 0, text: `**${RESPONSES.reasoningSummary}**`,
  });
  writeEvent(response, 'response.output_item.done', { item: TOOL_REASONING });
  const call = { type: 'function_call', id: CALL_ITEM_ID, call_id: RESPONSES.callId, name: RESPONSES.toolName };
  writeEvent(response, 'response.output_item.added', { item: { ...call, arguments: '' } });
  const argumentsJson = JSON.stringify({ text: RESPONSES.echo });
  const split = argumentsJson.indexOf('\\') + 1;
  for (const delta of [argumentsJson.slice(0, split), argumentsJson.slice(split)]) {
    writeEvent(response, 'response.function_call_arguments.delta', { item_id: call.id, delta });
  }
  writeEvent(response, 'response.output_item.done', { item: call });
  complete(response, [TOOL_REASONING, { ...call, arguments: argumentsJson }]);
}

export async function startResponsesMock() {
  const errors = [];
  const requests = [];
  let continuation;
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, PATH);
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const round = requests.push({
        authorization: request.headers.authorization,
        accept: request.headers.accept,
        body,
      });
      const laterAnswers = [RESPONSES.nextAnswer, RESPONSES.restartAnswer, RESPONSES.rotatedAnswer];
      assert.ok(round <= laterAnswers.length + 2, `Unexpected Responses request ${round}`);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      writeEvent(response, 'response.created', { response: { id: `resp_fixture_${round}` } });
      if (round === 1) {
        toolTurn(response);
      } else if (round === 2) {
        continuation = response;
        text(response, RESPONSES.answerStart);
      } else {
        text(response, laterAnswers[round - 3]);
        complete(response);
      }
    } catch (error) {
      errors.push(error);
      if (!response.headersSent) response.writeHead(500, { 'Content-Type': 'text/plain' });
      response.end(error.message);
    }
  });

  const port = await listenOnLoopback(server);
  let closing;
  return {
    endpoint: `http://127.0.0.1:${port}${PATH}`,
    requests,
    errors,
    releaseContinuation() {
      assert.ok(continuation && !continuation.destroyed && !continuation.writableEnded,
        'The second Responses stream must still be open');
      text(continuation, RESPONSES.answerEnd);
      complete(continuation, [ANSWER_REASONING]);
    },
    close: () => (closing ??= closeServer(server)),
  };
}
