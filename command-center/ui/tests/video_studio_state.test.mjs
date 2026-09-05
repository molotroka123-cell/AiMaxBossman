import test from 'node:test';
import assert from 'node:assert/strict';
import { ticks, seconds, duration, timecode, snapTime, commandEnvelope, filterMedia, activeSequence, selectedClip } from '../pages/video_studio_state.js';

test('microsecond times preserve precise fractional source boundaries', () => {
  assert.equal(ticks('1.000001'), 1000001); assert.equal(seconds(1000001), 1.000001);
  assert.throws(() => ticks(-1)); assert.throws(() => ticks(Infinity)); assert.throws(() => ticks('not time'));
});
test('duration uses rational speed, freeze and individual ramp segments', () => {
  assert.equal(duration({ source_in: 0, source_out: 3000000, speed: { num: 3, den: 2 } }), 2000000);
  assert.equal(duration({ freeze: true, freeze_duration: 900000 }), 900000);
  assert.equal(duration({ speed_ramp: [{ source_in: 0, source_out: 1000000, speed: { num: 1, den: 1 } }, { source_in: 1000000, source_out: 3000000, speed: { num: 2, den: 1 } }] }), 2000000);
});
test('timecode respects rational sequence fps rather than assuming 30', () => {
  assert.equal(timecode(1520000, { num: 25, den: 1 }), '00:00:01:13');
  assert.equal(timecode(3600000000, { num: 30000, den: 1001 }), '01:00:00:00');
});
const clip = { id: 'clip', start: 2000000, source_in: 0, source_out: 1000000, speed: { num: 1, den: 1 } };
const project = { id: 'project', revision: 7, active_sequence_id: 'second', sequences: [{ id: 'first', tracks: [] }, { id: 'second', tracks: [{ id: 'track', clips: [clip] }] }], markers: [{ t: 4000000 }] };
test('clip lookup and snap use active sequence and avoid self-snapping', () => {
  assert.equal(activeSequence(project).id, 'second'); assert.equal(selectedClip(project, 'clip').track.id, 'track');
  assert.equal(snapTime(3010000, project, null, 0, 20000), 3000000);
  assert.equal(snapTime(3010000, project, 'clip', 0, 20000), 3010000);
  assert.equal(snapTime(3990000, project, 'clip', 0, 20000), 4000000);
});
test('dry-run and apply retain the same id and source revision', () => {
  const command = { type: 'clip.split', clip_id: 'clip', at: 2500000 };
  const dry = commandEnvelope(project, command, 'op', true); const actual = commandEnvelope(project, command, 'op');
  assert.equal(dry.expected_revision, 7); assert.equal(dry.operation_id, actual.operation_id); assert.equal(dry.dry_run, true); assert.equal(actual.dry_run, false);
  assert.deepEqual(project.sequences[1].tracks[0].clips[0], clip);
  assert.throws(() => commandEnvelope({ id: 'p' }, command, 'op'));
});
test('media library searches tags, filters folders and sorts metadata without mutation', () => {
  const media = { a: { id: 'a', name: 'A', folder: 'one', tags: ['speech'], duration_ticks: 10, bytes: 20 }, b: { id: 'b', name: 'B', folder: 'two', tags: [], duration_ticks: 30, bytes: 10 } };
  assert.deepEqual(filterMedia(media, 'speech').map(m => m.id), ['a']);
  assert.deepEqual(filterMedia(media, '', 'two').map(m => m.id), ['b']);
  assert.deepEqual(filterMedia(media, '', '', 'duration').map(m => m.id), ['b', 'a']);
  assert.deepEqual(Object.keys(media), ['a', 'b']);
});
