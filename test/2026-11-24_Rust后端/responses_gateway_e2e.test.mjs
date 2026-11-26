// From the repository root, with Node 24+ and an already-built Rust binary:
//   node --test test/2026-11-24_Rust后端/responses_gateway_e2e.test.mjs
// Set ASTRION_HEADLESS_BIN when the binary is outside the default Cargo debug directory.
import assert from 'node:assert/strict';
import test from 'node:test';
import { COMPLETED, createTask, startGatewayFixture, streamedText } from './gateway_harness.mjs';
import { ANSWER_REASONING, RESPONSES, TOOL_REASONING, startResponsesMock } from './responses_gateway_mock.mjs';

const { apiKey, rotatedApiKey, model, callId, toolName, echo } = RESPONSES;
const firstAnswer = RESPONSES.answerStart + RESPONSES.answerEnd;

function user(text) {
  return { type: 'message', role: 'user', content: [{ type: 'input_text', text }] };
}

function assistant(text) {
  return { type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] };
}

function withoutServerId({ id, ...item }) {
  return item;
}

function assertResponsesRequest(request, key, expectedInput) {
  assert.ok(request, 'The task must POST a request to the Responses endpoint');
  const { authorization, accept, body } = request;
  assert.equal(authorization, `Bearer ${key}`);
  assert.equal(accept, 'text/event-stream');
  assert.equal(body.model, model);
  assert.equal(body.stream, true);
  assert.equal(body.store, false);
  assert.deepEqual(body.include, ['reasoning.encrypted_content']);
  assert.equal(body.tool_choice, 'auto');
  assert.ok(Array.isArray(body.tools));
  const tools = new Map(body.tools.map((tool) => [tool.name, tool]));
  assert.equal(tools.size, body.tools.length, 'Tool names must be unique');
  for (const name of [toolName, 'read_file', 'read_skill', 'search_project_memory']) {
    assert.equal(tools.get(name)?.type, 'function', `Expected ${name} in the Responses tool catalog`);
  }
  const echo = tools.get(toolName);
  assert.deepEqual(echo.parameters.required, ['text']);
  assert.equal(echo.parameters.properties.text.type, 'string');
  assert.equal(body.messages, undefined);
  assert.equal(body.previous_response_id, undefined);
  assert.doesNotMatch(JSON.stringify(body), /"(?:responses_reasoning_items|_responses_connection)"\s*:/);
  assert.ok(Array.isArray(body.input));
  const input = body.input.map((item) => item.type === 'function_call'
    ? { ...item, arguments: JSON.parse(item.arguments) }
    : item);
  assert.deepEqual(input, expectedInput);
}

async function readPublicHistory(gateway, conversationId) {
  const history = await gateway.getSessionHistory(conversationId);
  assert.equal(history.id, conversationId);
  const payload = JSON.stringify(history);
  assert.doesNotMatch(payload, /"(?:responses_reasoning_items|_responses_connection|encrypted_content)"\s*:/);
  for (const item of [TOOL_REASONING, ANSWER_REASONING]) {
    assert.ok(!payload.includes(item.encrypted_content), 'Public history must omit the opaque reasoning payload');
  }
  return history.messages;
}

async function finishTask(gateway, conversationId, prompt, answer) {
  const task = await createTask(gateway, conversationId, prompt);
  await task.until('a completed Responses task', (poll) => COMPLETED.has(poll.status));
  assert.equal(streamedText(task.events), answer);
}

test('real GatewayClient drives Responses tool continuation and keeps encrypted context private across restarts', { timeout: 45_000 }, async (t) => {
  const fixture = await startGatewayFixture(t, { model, apiKey, startMock: startResponsesMock });
  const { gateway, mock } = fixture;
  const freshInput = [user(RESPONSES.firstPrompt)];
  const toolInput = [
    { type: 'function_call', call_id: callId, name: toolName, arguments: { text: echo } },
    { type: 'function_call_output', call_id: callId, output: echo },
  ];
  const continuedInput = [...freshInput, withoutServerId(TOOL_REASONING), ...toolInput];
  const firstTaskInput = [...continuedInput, withoutServerId(ANSWER_REASONING), assistant(firstAnswer)];
  const nextExchange = [user(RESPONSES.nextPrompt), assistant(RESPONSES.nextAnswer)];

  try {
    const { conversation_id: conversationId } = await gateway.createSession();
    const first = await createTask(gateway, conversationId, RESPONSES.firstPrompt);
    const active = await first.until('visible text from the second Responses request',
      () => streamedText(first.events) === RESPONSES.answerStart);
    assert.equal(active.status, 'running');
    assert.equal(mock.requests.length, 2);
    assertResponsesRequest(mock.requests[0], apiKey, freshInput);
    assertResponsesRequest(mock.requests[1], apiKey, continuedInput);

    const started = first.events.findIndex((event) => event.type === 'tool_start');
    const completed = first.events.findIndex((event) =>
      event.type === 'update_action' && event.data.status === 'completed');
    const requestAgain = first.events.findLastIndex((event) => event.type === 'api_request_start');
    const firstText = first.events.findIndex((event) => event.type === 'text_chunk');
    assert.ok(started >= 0 && started < completed && completed < requestAgain && requestAgain < firstText,
      'The tool must finish before the continued Responses request and streamed answer');
    assert.equal(first.events[started].data.name, toolName);
    assert.equal(first.events[started].data.id, callId);
    assert.deepEqual(first.events[started].data.arguments, { text: echo });
    assert.equal(first.events[completed].data.id, callId);
    assert.equal(first.events[completed].data.result, echo);

    mock.releaseContinuation();
    await first.until('the completed Responses tool turn', (poll) => COMPLETED.has(poll.status));
    assert.equal(streamedText(first.events), firstAnswer);
    const firstHistory = await readPublicHistory(gateway, conversationId);
    assert.deepEqual(firstHistory.map((message) => message.role), ['user', 'assistant', 'tool', 'assistant']);
    assert.equal(firstHistory[0].content, RESPONSES.firstPrompt);
    assert.equal(firstHistory[1].reasoning_content, RESPONSES.reasoningSummary);
    assert.equal(firstHistory[1].tool_calls[0].id, callId);
    assert.equal(firstHistory[2].tool_call_id, callId);
    assert.equal(firstHistory[2].content, echo);
    assert.equal(firstHistory[3].content, firstAnswer);

    await finishTask(gateway, conversationId, RESPONSES.nextPrompt, RESPONSES.nextAnswer);
    assert.equal(mock.requests.length, 3);
    assertResponsesRequest(mock.requests[2], apiKey, [...firstTaskInput, nextExchange[0]]);
    const beforeRestart = await readPublicHistory(gateway, conversationId);
    assert.deepEqual(beforeRestart.slice(0, firstHistory.length), firstHistory);
    assert.equal(beforeRestart.at(-1).content, RESPONSES.nextAnswer);

    const restarted = await fixture.restart();
    assert.deepEqual(await readPublicHistory(restarted, conversationId), beforeRestart);
    assert.equal(mock.requests.length, 3, 'Reading recovered history must not call the model');
    await finishTask(restarted, conversationId, RESPONSES.restartPrompt, RESPONSES.restartAnswer);
    assert.equal(mock.requests.length, 4);
    assertResponsesRequest(mock.requests[3], apiKey, [...firstTaskInput, ...nextExchange, user(RESPONSES.restartPrompt)]);
    const beforeRotation = await readPublicHistory(restarted, conversationId);
    assert.deepEqual(beforeRotation.slice(0, beforeRestart.length), beforeRestart);
    assert.equal(beforeRotation.at(-1).content, RESPONSES.restartAnswer);

    const rotated = await fixture.restart({ apiKey: rotatedApiKey });
    assert.deepEqual(await readPublicHistory(rotated, conversationId), beforeRotation);
    assert.equal(mock.requests.length, 4);
    await finishTask(rotated, conversationId, RESPONSES.rotatedPrompt, RESPONSES.rotatedAnswer);
    assert.equal(mock.requests.length, 5);
    assertResponsesRequest(mock.requests[4], rotatedApiKey, [
      ...freshInput, ...toolInput, assistant(firstAnswer), ...nextExchange,
      user(RESPONSES.restartPrompt), assistant(RESPONSES.restartAnswer), user(RESPONSES.rotatedPrompt),
    ]);
    const finalHistory = await readPublicHistory(rotated, conversationId);
    assert.deepEqual(finalHistory.slice(0, beforeRotation.length), beforeRotation);
    assert.equal(finalHistory.at(-1).content, RESPONSES.rotatedAnswer);
  } catch (error) {
    if (mock.errors.length) {
      throw new AggregateError([error, ...mock.errors], `Responses mock failed: ${mock.errors[0].message}`);
    }
    throw error;
  }
  assert.deepEqual(mock.errors, []);
});
