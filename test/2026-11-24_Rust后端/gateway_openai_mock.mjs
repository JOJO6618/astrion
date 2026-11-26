import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { closeServer, listenOnLoopback } from './gateway_harness.mjs';

export const SCENARIO = Object.freeze({
  model: 'gateway-offline-model',
  tool: 'astrion_echo',
  toolCallId: 'call_gateway_echo',
  echo: '回声 "gateway" \\ 续轮',
  firstPrompt: '用回声工具验证离线续轮。',
  answerStart: '工具完成。',
  answerEnd: '离线验收通过。',
  cancelPrompt: '开始输出一段可以取消的回答。',
  cancelPartial: '取消前的片段。',
  recoveryPrompt: '取消后继续同一个会话。',
  recoveryAnswer: '同会话已恢复。',
});

const COMPLETIONS_PATH = '/v1/chat/completions';

function writeChunk(response, delta, finishReason = null) {
  const payload = {
    id: 'chatcmpl-gateway-offline',
    object: 'chat.completion.chunk',
    model: SCENARIO.model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function finish(response, reason = 'stop') {
  writeChunk(response, {}, reason);
  response.end('data: [DONE]\n\n');
}

function assertToolRound(messages) {
  const toolIndex = messages.findIndex((message) =>
    message.role === 'tool' && message.tool_call_id === SCENARIO.toolCallId,
  );
  assert.ok(toolIndex > 0, 'The continued request must carry the tool response linked to the call ID');
  const assistant = messages[toolIndex - 1];
  assert.equal(assistant.role, 'assistant');
  assert.equal(assistant.tool_calls.length, 1);
  const [call] = assistant.tool_calls;
  assert.equal(call.id, SCENARIO.toolCallId);
  assert.equal(call.type, 'function');
  assert.equal(call.function.name, SCENARIO.tool);
  assert.deepEqual(JSON.parse(call.function.arguments), { text: SCENARIO.echo });
  assert.equal(messages[toolIndex].content, SCENARIO.echo, 'The model must receive the exact echoed value');
  return toolIndex;
}

export async function startOpenAiMock() {
  const errors = [];
  const requests = [];
  let continuationResponse;
  let cancelStreamClosed = false;

  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, COMPLETIONS_PATH);
      assert.equal(request.headers.authorization, undefined, 'The mock must receive no provider credential');
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const callNumber = requests.push(payload);
      assert.equal(payload.model, SCENARIO.model);
      assert.equal(payload.stream, true);
      const { messages } = payload;
      if (callNumber === 1) {
        assert.equal(messages.at(-1).role, 'user');
        assert.equal(messages.at(-1).content, SCENARIO.firstPrompt);
        const tool = payload.tools?.find((entry) => entry.function?.name === SCENARIO.tool);
        assert.equal(tool?.type, 'function');
        assert.equal(tool.function.parameters.properties.text.type, 'string');
        assert.ok(tool.function.parameters.required.includes('text'));
      } else if (callNumber === 2) {
        assert.equal(assertToolRound(messages), messages.length - 1);
        assert.ok(messages.some((message) => message.role === 'user' && message.content === SCENARIO.firstPrompt));
      } else if (callNumber === 3 || callNumber === 4) {
        const toolIndex = assertToolRound(messages);
        assert.ok(messages.slice(toolIndex + 1).some((message) =>
          message.role === 'assistant' && message.content === SCENARIO.answerStart + SCENARIO.answerEnd,
        ), 'The next task must send the completed assistant turn back to the model');
        assert.equal(messages.at(-1).role, 'user');
        assert.equal(messages.at(-1).content, callNumber === 3 ? SCENARIO.cancelPrompt : SCENARIO.recoveryPrompt);
      } else {
        assert.fail(`Unexpected Chat Completions request ${callNumber}`);
      }

      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      writeChunk(response, { role: 'assistant' });
      if (callNumber === 1) {
        const args = JSON.stringify({ text: SCENARIO.echo });
        const split = args.indexOf('\\') + 1;
        writeChunk(response, { tool_calls: [{
          index: 0,
          id: SCENARIO.toolCallId,
          type: 'function',
          function: { name: SCENARIO.tool, arguments: args.slice(0, split) },
        }] });
        writeChunk(response, { tool_calls: [{ index: 0, function: { arguments: args.slice(split) } }] });
        finish(response, 'tool_calls');
      } else if (callNumber === 2) {
        continuationResponse = response;
        writeChunk(response, { content: SCENARIO.answerStart });
      } else if (callNumber === 3) {
        response.once('close', () => { cancelStreamClosed = !response.writableEnded; });
        writeChunk(response, { content: SCENARIO.cancelPartial });
      } else {
        writeChunk(response, { content: SCENARIO.recoveryAnswer });
        finish(response);
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
    endpoint: `http://127.0.0.1:${port}${COMPLETIONS_PATH}`,
    errors,
    requests,
    releaseContinuation() {
      assert.ok(continuationResponse && !continuationResponse.destroyed, 'The continued SSE stream must still be open');
      writeChunk(continuationResponse, { content: SCENARIO.answerEnd });
      finish(continuationResponse);
    },
    get cancelStreamClosed() { return cancelStreamClosed; },
    close: () => (closing ??= closeServer(server)),
  };
}
