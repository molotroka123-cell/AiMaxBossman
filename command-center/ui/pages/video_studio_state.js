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
