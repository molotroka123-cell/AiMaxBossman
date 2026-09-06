/* Pure timeline math shared by the editor and its tests. No media in memory. */
export const TIMEBASE = 1000000;
export function seconds(ticks) { return Number(ticks || 0) / TIMEBASE; }
export function ticks(value) {
  const result = Math.round(Number(value) * TIMEBASE);
  if (!Number.isSafeInteger(result) || result < 0) throw new Error('Invalid nonnegative time');
  return result;
}
export function duration(clip) {
  if (clip.freeze || clip.title) return clip.freeze_duration || 0;
  if (clip.speed_ramp?.length) return clip.speed_ramp.reduce((n, x) => n + Math.round((x.source_out - x.source_in) * x.speed.den / x.speed.num), 0);
  return Math.round((clip.source_out - clip.source_in) * (clip.speed?.den || 1) / (clip.speed?.num || 1));
}
export function activeSequence(project) {
  return project?.sequences?.find(s => s.id === project.active_sequence_id) || project?.sequences?.[0];
}
export function selectedClip(project, id) {
  for (const track of activeSequence(project)?.tracks || []) {
    const clip = track.clips.find(c => c.id === id);
    if (clip) return { clip, track };
  }
  return null;
}
export function endTime(project) {
  return Math.max(0, ...(activeSequence(project)?.tracks || []).flatMap(t => t.clips.map(c => c.start + duration(c))));
}
export function snapTime(value, project, excludedId, playhead, threshold) {
  const points = [0, playhead, ...(project?.markers || []).map(m => m.time ?? m.t ?? 0)];
  for (const track of activeSequence(project)?.tracks || []) for (const clip of track.clips) {
    if (clip.id !== excludedId) points.push(clip.start, clip.start + duration(clip));
  }
  const nearest = points.filter(p => Math.abs(p - value) <= threshold)
    .sort((a, b) => Math.abs(a - value) - Math.abs(b - value))[0];
  return Math.max(0, nearest === undefined ? value : nearest);
}
export function timecode(value, fps = { num: 25, den: 1 }) {
  const total = seconds(value);
  const rate = fps.num / fps.den;
  const frame = Math.floor((total - Math.floor(total)) * rate + 1e-6);
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, Math.floor(total) % 60, frame].map(n => String(n).padStart(2, '0')).join(':');
}
export function commandEnvelope(project, command, operationId, dryRun = false) {
  if (!project?.id || !Number.isSafeInteger(project.revision)) throw new Error('Project revision required');
  return { project_id: project.id, expected_revision: project.revision, operation_id: operationId, command, dry_run: dryRun };
}
export function filterMedia(media, query = '', folder = '', sort = 'name') {
  const q = query.toLocaleLowerCase();
  return Object.values(media || {}).filter(m => (!folder || m.folder === folder)
    && [m.name, m.folder, ...(m.tags || [])].join(' ').toLocaleLowerCase().includes(q))
    .sort((a, b) => sort === 'duration' ? b.duration_ticks - a.duration_ticks : sort === 'size' ? b.bytes - a.bytes : a.name.localeCompare(b.name));
}

// Blank dimensions deliberately preserve the renderer's selected profile.
export function exportOptions(fields) {
  const options = { profile: fields.profile || 'source', crf: Number(fields.crf ?? 20) };
  for (const key of ['width', 'height']) if (String(fields[key] ?? '').trim()) {
    const value = Number(fields[key]);
    if (!Number.isInteger(value) || value < 16 || value > 8192 || value % 2) throw new Error('Export dimensions must be even integers, 16–8192');
    options[key] = value;
  }
  if (fields.fps_num || fields.fps_den) {
    const num = Number(fields.fps_num), den = Number(fields.fps_den);
    if (!Number.isSafeInteger(num) || !Number.isSafeInteger(den) || num <= 0 || den <= 0 || num / den > 240) throw new Error('Invalid export FPS');
    options.fps = { num, den };
  }
  for (const key of ['video_codec', 'audio_codec', 'bitrate', 'audio_bitrate', 'container', 'mode']) if (fields[key]) options[key] = fields[key];
  if (fields.range_mode === 'range') {
    options.range = { start: ticks(fields.range_start), end: ticks(fields.range_end) };
    if (options.range.end <= options.range.start) throw new Error('Export range must have positive duration');
  }
  return options;
}

export function captionText(cues, format = 'srt') {
  const time = value => {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error('Invalid caption time');
    const ms = Math.round(value / 1000);
    return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}${format === 'vtt' ? '.' : ','}${String(ms % 1000).padStart(3, '0')}`;
  };
  const body = cues.map((cue, index) => {
    if (cue.end <= cue.start) throw new Error('Caption end must follow start');
    return `${index + 1}\n${time(cue.start)} --> ${time(cue.end)}\n${cue.text}\n`;
  }).join('\n');
  return format === 'vtt' ? `WEBVTT\n\n${body}` : body;
}

export function proposalBindingMatches(request, project, selected, draftText) {
  return !!request && project?.id === request.projectId && project.revision === request.revision
    && selected === request.selected && draftText === request.draftText;
}

// Shared sequence-frame commands: exact integer arithmetic matches backend
// half-up rounding, including 24000/1001 and 30000/1001 sequences.
function frameRate(fps) {
  if (!fps || !Number.isSafeInteger(fps.num) || !Number.isSafeInteger(fps.den)
      || fps.num < 1 || fps.den < 1 || fps.num > 100000 || fps.den > 100000
      || fps.num / fps.den < .01 || fps.num / fps.den > 240) throw new Error('Invalid sequence FPS');
  return { num: BigInt(fps.num), den: BigInt(fps.den) };
}
const roundRatio = (num, den) => Number((2n * num + den) / (2n * den));
export function frameIndex(value, fps) {
  if (!Number.isSafeInteger(value) || value < 0 || value > 7 * 86400 * TIMEBASE) throw new Error('Invalid frame position');
  const rate = frameRate(fps);
  return roundRatio(BigInt(value) * rate.num, BigInt(TIMEBASE) * rate.den);
}
export function frameTicks(frame, fps) {
  if (!Number.isSafeInteger(frame) || frame < 0) throw new Error('Invalid frame index');
  const rate = frameRate(fps);
  const value = roundRatio(BigInt(frame) * BigInt(TIMEBASE) * rate.den, rate.num);
  if (!Number.isSafeInteger(value) || value > 7 * 86400 * TIMEBASE) throw new Error('Frame exceeds timeline limit');
  return value;
}
export function splitFrameCommand(project, clipId, playhead) {
  const sequence = activeSequence(project);
  const selected = selectedClip(project, clipId);
  if (!selected || selected.track.locked) throw new Error('Select an unlocked clip');
  const frame = frameIndex(playhead, sequence.fps);
  const at = frameTicks(frame, sequence.fps);
  if (at <= selected.clip.start || at >= selected.clip.start + duration(selected.clip)) throw new Error('Frame must be inside the clip');
  return { type: 'clip.split', clip_id: clipId, frame, with_links: true };
}
