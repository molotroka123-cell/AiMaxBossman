# Video Studio shared implementation contract

BASE_SHA: `debee6930f29595b84cea64d526b6f8bef139e8a`. Isolated branch `codex/video-studio`.
The original checkout has unrelated untracked files; they are not imported or overwritten.
At this baseline Reality Compiler is absent from tracked integration. Use existing BCC policy, task engine, audit and verification; document the gap.

## Canonical project document (schema_version 1)

Time values are integer microsecond ticks (`timebase=1000000`). Frames use rational `fps={num:25,den:1}`; source and sequence time are separate. Export explicitly normalizes VFR sources to sequence CFR, square pixels, BT.709 SDR, stereo/48 kHz unless overridden by a validated export profile. Originals are immutable.

Project: `{id,name,schema_version:1,timebase:1000000,revision:0,archived:false,active_sequence_id,media:{id:Media},sequences:[Sequence],markers:[],captions:[],links:{},layout:{},metadata:{}}`.
Sequence: `{id,name,width:1280,height:720,fps:{num:25,den:1},sample_rate:48000,tracks:[Track],range:null}`.
Track: `{id,name,kind:'video'|'audio'|'adjustment',mute:false,solo:false,locked:false,volume:1.0,pan:0,clips:[]}`.
Clip: `{id,media_id,start,source_in,source_out,speed:{num:1,den:1},reverse:false,freeze:false,transform:{x:0,y:0,scale:1,rotation:0,opacity:1,crop:[0,0,0,0]},effects:[],keyframes:{},group_id:null,linked_id:null}`.
Clip duration is `(source_out-source_in)*speed.den/speed.num`, rounded to integer ticks; freeze clips use `freeze_duration`. Ramp clips have `speed_ramp:[{source_in,source_out,speed:{num,den}}]`; their duration is the sum of segment durations. `nested_sequence_id` may replace `media_id`; cycles are invalid. Title clips use `title:{text,font,size,color,background,align}` and `freeze_duration`.
Media: `{id,name,relative_path,sha256,bytes,duration_ticks,width,height,fps:{num,den},has_video,has_audio,sample_rate,channels,folder:'',tags:[],metadata:{}}`. Paths are host-issued, confined to the Video Studio data root. The document has no arbitrary URL input.
Effect: `{id,type,params,enabled:true}`. Keyframe map keys identify a numeric parameter, values are `[{t,value,easing:'linear'|'hold'|'ease_in'|'ease_out'|'ease_in_out'}]` where t is clip-local timeline ticks. Parameter whitelist is mandatory before renderer expression generation.

## Shared command layer

`POST /api/video-studio/commands` input `{project_id,expected_revision,operation_id,command:{type,...},dry_run:false}`.
Output `{project_id,revision,project,changed_ids,warnings,artifacts,undo:{available,operation_id},dry_run}`. UI and agent call the same service. Every mutation is atomic, revision guarded, recorded in history; identical operation_id+payload replays the original result, altered payload conflicts. A dry run does not consume operation_id or revision. Human edit leases protect object IDs from agent commands; they do not move selection/playhead.

Root ownership: `bcc/video_studio/model.py`, `commands.py`, `store.py`, their tests and capability matrix.
Integration ownership: `bcc/features/video_studio.py`, `bcc/video_studio/service.py`, `tools.py`, native chat/tab and TaskEngine hooks, integration tests, skill.
Renderer ownership: `bcc/video_studio/media.py`, `render.py`, `analysis.py`, real FFmpeg tests.
Frontend ownership: `command-center/ui/pages/video_studio.js`, `video_studio.css`, UI-specific helpers/tests and UX spec. Coordinate page registry edits with integration owner.

## Host interfaces

Root `ProjectStore(db)` uses canonical BCC SQLAlchemy db/session and metadata. Async methods `create(project_id,name,operation_id,links=None)`, `get(project_id)`, `list(archived=False)`, `apply(project_id,expected_revision,operation_id,command,actor='human',dry_run=False)`, `history(project_id)`. Create returns the same command-result envelope at revision 0. `get` returns project document. Root `apply_command(project,command,actor='human')` is pure and returns `(new_project,changed_ids,warnings)`; export/media preprocessing belongs service before admission.

Renderer `MediaLibrary(root)` accepts only host-approved local file objects/paths from the upload/attachment service. `async import_file(path,name=None)` copies/streams into content-addressed storage and returns Media. `resolve(media)` validates confinement/hash references. `async prepare(media)` creates cached thumbnail, waveform and proxy and returns metadata/relative artifact paths. `capabilities()` reports actual binary/filter/codec availability. No cloud calls.

Renderer `async render_project(project,root,output_path,options=None,progress=None)` consumes an immutable project revision, uses argv-only FFmpeg, calls `await progress(stage,details)` when provided, cancels and awaits its subprocess on CancelledError, returns `{path,verification,profile}` only after independent ffprobe/decode checks. `verify_output(path,expected)` returns actual measurements and pass/fail obligations. Preview calls the same renderer with a small output profile/range. No queue in renderer: canonical TaskEngine owns dispatch, leases, cancellation, retries, progress and recovery.

All advanced capability claims require actual operation, rendering/support semantics, UI and evidence. Missing external providers/models are reported explicitly, never substituted with generated demo media.
