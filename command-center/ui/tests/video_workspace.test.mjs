import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../api.js';
import { ChatPage, trackVideoProject, routeVideoRequest } from '../video_chat.js';
import VideoStudioPage from '../pages/video_studio.js';

test('one primary editor; chat history stays reachable without a second editor', () => {
  assert.equal(VideoStudioPage.id, 'video-studio');
  assert.equal(VideoStudioPage.nav, 'primary');
  assert.equal(ChatPage.id, 'bossman-chat');
  assert.equal(ChatPage.nav, 'more');
});

test('project continuity needs no DOM and survives route changes and editor selection', async () => {
  const events = new EventTarget();
  let hash = '#/video-studio?project_id=project%20one';
  const stop = trackVideoProject(events, () => hash);
  const oldRaw = api.raw;
  const payloads = [];
  api.raw = async (url, options) => {
    assert.equal(url, '/api/video-studio/chat');
    payloads.push(options.body);
    return { handled: false };
  };
  try {
    await routeVideoRequest('open video', [], {});
    assert.equal(payloads.at(-1).project_id, 'project one');
    hash = '#/home?project_id=unrelated';
    events.dispatchEvent(new Event('hashchange'));
    await routeVideoRequest('открой видео', [], {});
    assert.equal(payloads.at(-1).project_id, 'project one');
    const opened = new Event('bcc:video-open');
    opened.detail = { project_id: 'selected-in-editor' };
    events.dispatchEvent(opened);
    await routeVideoRequest('open', [], {});
    assert.equal(payloads.at(-1).project_id, 'selected-in-editor');
    await routeVideoRequest('opening titles', [], {});
    assert.equal('project_id' in payloads.at(-1), false);
    stop();
    hash = '#/video-studio?project_id=after-disposal';
    events.dispatchEvent(new Event('hashchange'));
    await routeVideoRequest('open video', [], {});
    assert.equal(payloads.at(-1).project_id, 'selected-in-editor');
  } finally { stop(); api.raw = oldRaw; }
});
