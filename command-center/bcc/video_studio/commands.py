"""Atomic, non-destructive operations shared by the human UI and agent tools."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction

from .model import (Conflict, EditLocked, MissingObject, StudioError, TICKS, clip, clip_duration,
                    identity, label, new_clip, new_sequence, new_track, number, rate, sequence,
                    sequence_duration, ticks, track, uid, validate_project)


def _mutable(tr):
    if tr["locked"]:
        raise EditLocked("Unlock the track before editing its objects")


def _clip(project, key):
    seq, tr, value = clip(project, key)
    _mutable(tr)
    return seq, tr, value


def _shift(tr, at, delta, exclude=()):
    _mutable(tr)
    for other in tr["clips"]:
        if other["id"] not in exclude and other["start"] >= at:
            other["start"] = ticks(other["start"] + delta)


def _source_delta(c, timeline_delta):
    if c.get("speed_ramp") or c.get("freeze") or c.get("title"):
        raise StudioError("This edit requires a constant-speed source clip; flatten its ramp first")
    value = timeline_delta * rate(c["speed"])
    return round(value)


def _patch(value, patch, allowed):
    if not isinstance(patch, dict) or set(patch) - set(allowed):
        raise StudioError("Unknown or immutable property in patch")
    value.update(deepcopy(patch))


def _effect_list(p, cmd):
    if cmd.get("clip_id"):
        _, _, owner = _clip(p, cmd["clip_id"])
    elif cmd.get("track_id"):
        _, owner = track(p, cmd["track_id"])
        _mutable(owner)
    else:
        raise StudioError("Effect needs a clip_id or track_id")
    return owner, owner.setdefault("effects", [])


def _duration_trim(c, new_duration):
    c["source_out"] = c["source_in"] + _source_delta(c, new_duration)


def _split(tr, c, at):
    delta = ticks(at) - c["start"]
    duration = clip_duration(c)
    if not 0 < delta < duration:
        raise StudioError("Split position must be strictly inside clip")
    source_delta = _source_delta(c, delta)
    right = deepcopy(c)
    right.update(id=uid(), start=at)
    if c.get("reverse"):
        cut = c["source_out"] - source_delta
        right["source_out"] = cut
        c["source_in"] = cut
    else:
        cut = c["source_in"] + source_delta
        right["source_in"] = cut
        c["source_out"] = cut
    # Preserve the interpolation at the cut, not two different abrupt states.
    for name, keys in c.get("keyframes", {}).items():
        cut_value = interpolate(keys, delta)
        c["keyframes"][name] = [k for k in keys if k["t"] < delta] + [{"t": delta, "value": cut_value, "easing": "linear"}]
        right["keyframes"][name] = [{"t": 0, "value": cut_value, "easing": "linear"}] + [dict(k, t=k["t"]-delta) for k in keys if k["t"] > delta]
    tr["clips"].insert(tr["clips"].index(c) + 1, right)
    return right


def interpolate(keys, t):
    if not keys:
        raise StudioError("Cannot interpolate an empty keyframe curve")
    if t <= keys[0]["t"]:
        return keys[0]["value"]
    for left, right in zip(keys, keys[1:]):
        if t <= right["t"]:
            f = (t-left["t"])/(right["t"]-left["t"])
            easing = left.get("easing", "linear")
            f = {"hold": lambda x: 0, "linear": lambda x: x, "ease_in": lambda x: x*x,
                 "ease_out": lambda x: 1-(1-x)**2, "ease_in_out": lambda x: x*x*(3-2*x)}[easing](f)
            return left["value"] + f*(right["value"]-left["value"])
    return keys[-1]["value"]


def apply_command(project, command, actor="human"):
    """Pure application. The store owns revision/CAS/history and edit leases."""
    if not isinstance(command, dict) or not isinstance(command.get("type"), str):
        raise StudioError("Command requires a type")
    p, changed, warnings = deepcopy(project), [], []
    if p.get("archived") and command["type"] not in ("project.archive", "project.rename"):
        raise EditLocked("Restore the archived project before editing")
    cmd = deepcopy(command)
    kind = cmd["type"]
    if kind.startswith("video."):
        kind = kind[6:]
    if kind == "timeline.apply":
        operations = cmd.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 256:
            raise StudioError("Timeline batch needs 1..256 operations")
        for operation in operations:
            if operation.get("type") in ("timeline.apply", "video.timeline.apply"):
                raise StudioError("Nested batches are unsupported")
            p, ids, notes = apply_command(p, operation, actor)
            changed.extend(ids)
            warnings.extend(notes)
    elif kind == "project.import":
        # Host service must validate every referenced media file before this
        # command; generic HTTP/model mutation endpoints reject this type.
        imported = deepcopy(cmd["project"])
        imported.update(id=p["id"], revision=p["revision"], links=p["links"])
        validate_project(imported)
        p = imported
        changed.append(p["id"])
    elif kind == "project.rename":
        p["name"] = label(cmd["name"])
        changed.append(p["id"])
    elif kind == "project.archive":
        if type(cmd.get("archived", True)) is not bool:
            raise StudioError("Archive state must be boolean")
        p["archived"] = cmd.get("archived", True)
        changed.append(p["id"])
    elif kind == "project.layout":
        _patch(p["layout"], cmd["layout"], {"workspace", "language", "left_width", "right_width", "timeline_height", "agent_width", "agent_collapsed"})
        changed.append(p["id"])
    elif kind == "sequence.add":
        seq = new_sequence(cmd.get("name", "Последовательность"), cmd.get("id"))
        p["sequences"].append(seq)
        changed.append(seq["id"])
    elif kind in ("sequence.update", "sequence.settings"):
        seq = sequence(p, cmd.get("sequence_id"))
        _patch(seq, cmd.get("patch", {}), {"name", "width", "height", "fps", "sample_rate"})
        changed.append(seq["id"])
    elif kind == "sequence.select":
        p["active_sequence_id"] = sequence(p, cmd["sequence_id"])["id"]
        changed.append(p["active_sequence_id"])
    elif kind == "sequence.duplicate":
        seq = deepcopy(sequence(p, cmd.get("sequence_id")))
        seq.update(id=uid(), name=label(cmd.get("name", seq["name"]+" — копия")))
        for tr in seq["tracks"]:
            tr["id"] = uid()
            for c in tr["clips"]:
                c.update(id=uid(), group_id=None, linked_id=None)
        p["sequences"].append(seq)
        changed.append(seq["id"])
    elif kind == "media.import":
        media = deepcopy(cmd["media"])
        key = identity(media["id"])
        if key in p["media"] and p["media"][key]["sha256"] != media["sha256"]:
            raise Conflict("A media identity cannot be replaced during import")
        p["media"][key] = media
        changed.append(key)
    elif kind == "media.update":
        key = cmd["media_id"]
        if key not in p["media"]:
            raise MissingObject("Unknown media ID")
        _patch(p["media"][key], cmd["patch"], {"name", "folder", "tags"})
        label(p["media"][key]["name"])
        tags = p["media"][key].get("tags", [])
        if not isinstance(tags, list) or len(tags) > 50:
            raise StudioError("Media supports at most 50 tags")
        for tag in tags:
            label(tag, 80)
        changed.append(key)
    elif kind == "media.relink":
        key = cmd["media_id"]
        if key not in p["media"]:
            raise MissingObject("Unknown media ID")
        replacement = deepcopy(cmd["media"])
        replacement["id"] = key
        p["media"][key] = replacement
        changed.append(key)
        warnings.append("Relink replaced the media reference; original source files were not modified")
    elif kind == "track.add":
        seq = sequence(p, cmd.get("sequence_id"))
        item = new_track(cmd.get("kind", "video"), cmd.get("name"), cmd.get("id"))
        seq["tracks"].append(item)
        changed.append(item["id"])
    elif kind == "track.update":
        _, tr = track(p, cmd["track_id"])
        if tr["locked"] and set(cmd["patch"]) - {"locked"}:
            raise EditLocked("Only unlocking is allowed on a locked track")
        _patch(tr, cmd["patch"], {"name", "mute", "solo", "locked", "volume", "pan"})
        changed.append(tr["id"])
    elif kind == "track.move":
        seq, tr = track(p, cmd["track_id"])
        _mutable(tr)
        index = cmd["index"]
        if type(index) is not int or not 0 <= index < len(seq["tracks"]):
            raise StudioError("Invalid track index")
        seq["tracks"].remove(tr)
        seq["tracks"].insert(index, tr)
        changed.append(tr["id"])
    elif kind == "track.remove":
        seq, tr = track(p, cmd["track_id"])
        _mutable(tr)
        seq["tracks"].remove(tr)
        changed.extend([tr["id"], *[c["id"] for c in tr["clips"]]])
    elif kind in ("clip.add", "title.add"):
        seq, tr = track(p, cmd["track_id"])
        _mutable(tr)
        values = deepcopy(cmd.get("clip", {}))
        if kind == "title.add":
            values.update(title=cmd["title"], freeze_duration=cmd.get("duration", 3*TICKS),
                          source_in=0, source_out=cmd.get("duration", 3*TICKS), start=cmd.get("start", 0))
        elif "source_out" not in values and values.get("media_id") in p["media"]:
            values["source_out"] = p["media"][values["media_id"]]["duration_ticks"]
        item = new_clip(values)
        tr["clips"].append(item)
        changed.append(item["id"])
    elif kind == "clip.move":
        seq, tr, c = _clip(p, cmd["clip_id"])
        start = ticks(cmd["start"])
        delta = start-c["start"]
        linked = [c]
        if cmd.get("with_group", True) and c.get("group_id"):
            linked = [other for t in seq["tracks"] for other in t["clips"] if other.get("group_id") == c["group_id"]]
        for item in linked:
            _, owning, _ = _clip(p, item["id"])
            item["start"] = ticks(item["start"]+delta)
            changed.append(item["id"])
        if cmd.get("track_id") and cmd["track_id"] != tr["id"]:
            seq2, dest = track(p, cmd["track_id"])
            _mutable(dest)
            if seq2["id"] != seq["id"]:
                raise StudioError("Use copy to move between sequences")
            tr["clips"].remove(c)
            dest["clips"].append(c)
    elif kind == "clip.trim":
        _, tr, c = _clip(p, cmd["clip_id"])
        before = clip_duration(c)
        if "source_in" in cmd:
            c["source_in"] = ticks(cmd["source_in"])
        if "source_out" in cmd:
            c["source_out"] = ticks(cmd["source_out"])
        after = clip_duration(c)
        c["keyframes"] = {name: [k for k in keys if k["t"] <= after] for name, keys in c.get("keyframes", {}).items()}
        if cmd.get("ripple", False):
            _shift(tr, c["start"]+before, after-before, (c["id"],))
            changed.extend(other["id"] for other in tr["clips"])
        changed.append(c["id"])
    elif kind == "clip.split":
        _, tr, c = _clip(p, cmd["clip_id"])
        other = _split(tr, c, ticks(cmd["at"]))
        changed.extend([c["id"], other["id"]])
    elif kind in ("clip.remove", "clip.ripple_delete"):
        _, tr, c = _clip(p, cmd["clip_id"])
        tr["clips"].remove(c)
        if cmd.get("ripple", kind == "clip.ripple_delete"):
            _shift(tr, c["start"]+clip_duration(c), -clip_duration(c))
            changed.extend(other["id"] for other in tr["clips"])
        changed.append(c["id"])
    elif kind == "clip.roll":
        _, tr, left = _clip(p, cmd["clip_id"])
        _, tr2, right = _clip(p, cmd["right_clip_id"])
        if tr["id"] != tr2["id"] or left["start"]+clip_duration(left) != right["start"]:
            raise StudioError("Roll requires adjacent clips on the same track")
        delta = cmd["delta"]
        if type(delta) is not int:
            raise StudioError("Roll delta must be integer ticks")
        left["source_out"] += _source_delta(left, delta)
        right["source_in"] += _source_delta(right, delta)
        right["start"] = ticks(right["start"]+delta)
        changed.extend([left["id"], right["id"]])
    elif kind == "clip.slip":
        _, _, c = _clip(p, cmd["clip_id"])
        delta = cmd["delta"]
        if type(delta) is not int:
            raise StudioError("Slip delta must be integer source ticks")
        c["source_in"] = ticks(c["source_in"]+delta)
        c["source_out"] = ticks(c["source_out"]+delta)
        changed.append(c["id"])
    elif kind == "clip.slide":
        _, tr, c = _clip(p, cmd["clip_id"])
        delta = cmd["delta"]
        if type(delta) is not int:
            raise StudioError("Slide delta must be integer timeline ticks")
        left = next((x for x in tr["clips"] if x["start"]+clip_duration(x) == c["start"] and x is not c), None)
        right = next((x for x in tr["clips"] if x["start"] == c["start"]+clip_duration(c) and x is not c), None)
        if not left or not right:
            raise StudioError("Slide requires adjacent clips on both sides")
        left["source_out"] += _source_delta(left, delta)
        c["start"] = ticks(c["start"]+delta)
        right["source_in"] += _source_delta(right, delta)
        right["start"] = ticks(right["start"]+delta)
        changed.extend([left["id"], c["id"], right["id"]])
    elif kind == "clip.copy":
        _, _, c = _clip(p, cmd["clip_id"])
        _, dest = track(p, cmd["track_id"])
        _mutable(dest)
        item = deepcopy(c)
        item.update(id=uid(), start=ticks(cmd["start"]), group_id=None, linked_id=None)
        dest["clips"].append(item)
        changed.append(item["id"])
    elif kind == "clip.detach_audio":
        seq, _, c = _clip(p, cmd["clip_id"])
        media = p["media"][c["media_id"]]
        if not media["has_audio"]:
            raise StudioError("Source has no audio stream")
        _, dest = track(p, cmd["track_id"])
        _mutable(dest)
        if dest["kind"] != "audio":
            raise StudioError("Detached audio needs an audio track")
        item = deepcopy(c)
        group = c.get("group_id") or uid()
        item.update(id=uid(), group_id=group, linked_id=c["id"], audio_only=True)
        c.update(group_id=group, linked_id=item["id"], audio_disabled=True)
        dest["clips"].append(item)
        changed.extend([c["id"], item["id"]])
    elif kind in ("clip.group", "clip.ungroup", "clip.link"):
        ids = cmd["clip_ids"]
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise StudioError("Group requires unique clip IDs")
        group = None if kind == "clip.ungroup" else uid()
        for key in ids:
            _, _, c = _clip(p, key)
            c["group_id"] = group
            if kind == "clip.link":
                c["linked_id"] = next((x for x in ids if x != key), None)
            changed.append(key)
    elif kind in ("clip.speed", "clip.reverse", "clip.freeze", "clip.speed_ramp"):
        _, tr, c = _clip(p, cmd["clip_id"])
        before = clip_duration(c)
        if kind == "clip.speed":
            rate(cmd["speed"])
            c["speed"] = cmd["speed"]
            c.pop("speed_ramp", None)
        elif kind == "clip.reverse":
            if type(cmd.get("reverse", True)) is not bool:
                raise StudioError("Reverse state must be boolean")
            c["reverse"] = cmd.get("reverse", True)
        elif kind == "clip.freeze":
            c.update(freeze=True, source_in=ticks(cmd["at_source"]), freeze_duration=ticks(cmd["duration"]))
        else:
            segments = cmd["segments"]
            if not isinstance(segments, list) or not 1 <= len(segments) <= 64:
                raise StudioError("Speed ramp requires 1..64 segments")
            if segments[0]["source_in"] != c["source_in"] or segments[-1]["source_out"] != c["source_out"]:
                raise StudioError("Speed ramp must cover the full source range")
            if any(a["source_out"] != b["source_in"] for a,b in zip(segments, segments[1:])):
                raise StudioError("Speed ramp segments must be contiguous")
            c["speed_ramp"] = segments
        if cmd.get("ripple", False):
            _shift(tr, c["start"]+before, clip_duration(c)-before, (c["id"],))
        changed.append(c["id"])
    elif kind == "clip.transform":
        _, _, c = _clip(p, cmd["clip_id"])
        _patch(c["transform"], cmd["patch"], {"x", "y", "scale", "rotation", "opacity", "crop"})
        changed.append(c["id"])
    elif kind == "clip.update":
        _, _, c = _clip(p, cmd["clip_id"])
        patch = deepcopy(cmd["patch"])
        if "transform" in patch:
            _patch(c["transform"], patch.pop("transform"), {"x", "y", "scale", "rotation", "opacity", "crop"})
        _patch(c, patch, {"volume", "pan", "audio_disabled", "title"})
        number(c.get("volume", 1), 0, 16)
        number(c.get("pan", 0), -1, 1)
        changed.append(c["id"])
    elif kind in ("effect.apply", "audio.process"):
        owner, effects = _effect_list(p, cmd)
        effect = deepcopy(cmd["effect"])
        effect.setdefault("id", uid())
        effect.setdefault("enabled", True)
        identity(effect["id"])
        label(effect["type"], 80)
        if not isinstance(effect.get("params", {}), dict) or type(effect["enabled"]) is not bool:
            raise StudioError("Effect requires typed parameters and enabled flag")
        effect.setdefault("params", {})
        current = next((x for x in effects if x["id"] == effect["id"]), None)
        if current:
            effects[effects.index(current)] = effect
        else:
            effects.append(effect)
        changed.extend([owner["id"], effect["id"]])
    elif kind == "effect.remove":
        owner, effects = _effect_list(p, cmd)
        item = next((x for x in effects if x["id"] == cmd["effect_id"]), None)
        if item is None:
            raise MissingObject("Unknown effect ID")
        effects.remove(item)
        changed.extend([owner["id"], item["id"]])
    elif kind == "effect.copy":
        _, _, source = _clip(p, cmd["source_clip_id"])
        for key in cmd["target_clip_ids"]:
            _, _, c = _clip(p, key)
            c["effects"] = [dict(deepcopy(f), id=uid()) for f in source["effects"]]
            changed.append(key)
    elif kind == "keyframe.set":
        _, _, c = _clip(p, cmd["clip_id"])
        param = label(cmd["param"], 80)
        keys = c["keyframes"].setdefault(param, [])
        key = {"t": ticks(cmd["t"]), "value": number(cmd["value"]), "easing": cmd.get("easing", "linear")}
        keys[:] = sorted([x for x in keys if x["t"] != key["t"]] + [key], key=lambda x:x["t"])
        changed.append(c["id"])
    elif kind == "keyframe.remove":
        _, _, c = _clip(p, cmd["clip_id"])
        keys = c["keyframes"].get(cmd["param"], [])
        c["keyframes"][cmd["param"]] = [k for k in keys if k["t"] != cmd["t"]]
        changed.append(c["id"])
    elif kind == "clip.audio":
        _, _, c = _clip(p, cmd["clip_id"])
        _patch(c, cmd["patch"], {"volume", "pan", "audio_disabled", "fade_in", "fade_out"})
        number(c.get("volume", 1), 0, 16)
        number(c.get("pan", 0), -1, 1)
        changed.append(c["id"])
    elif kind == "marker.add":
        mark = deepcopy(cmd["marker"])
        mark.setdefault("id", uid())
        identity(mark["id"])
        ticks(mark["t"])
        mark["label"] = label(mark.get("label", "Маркер"))
        if any(x["id"] == mark["id"] for x in p["markers"]):
            raise Conflict("Duplicate marker ID")
        p["markers"].append(mark)
        changed.append(mark["id"])
    elif kind == "marker.remove":
        key = cmd["marker_id"]
        if not any(x["id"] == key for x in p["markers"]):
            raise MissingObject("Unknown marker ID")
        p["markers"] = [x for x in p["markers"] if x["id"] != key]
        changed.append(key)
    elif kind == "range.set":
        seq = sequence(p, cmd.get("sequence_id"))
        start, end = ticks(cmd["start"]), ticks(cmd["end"])
        if start >= end or end > sequence_duration(seq):
            raise StudioError("Range must lie inside sequence duration")
        seq["range"] = {"start": start, "end": end}
        changed.append(seq["id"])
    elif kind in ("captions.edit", "captions.replace"):
        captions = deepcopy(cmd["captions"])
        if not isinstance(captions, list) or len(captions) > 10000:
            raise StudioError("Captions must be a list of at most 10000 cues")
        for item in captions:
            item.setdefault("id", uid())
            identity(item["id"])
            if ticks(item["start"]) >= ticks(item["end"]):
                raise StudioError("Caption end must follow its start")
            label(item["text"], 10000)
        if len({c["id"] for c in captions}) != len(captions):
            raise StudioError("Caption IDs must be unique")
        p["captions"] = captions
        changed.extend(c["id"] for c in captions)
    elif kind == "transcript.cut":
        # Keep explicitly selected transcript time ranges; each range becomes
        # a source clip and advances the target track without changing originals.
        _, tr, c = _clip(p, cmd["clip_id"])
        ranges = cmd["ranges"]
        if not isinstance(ranges, list) or not ranges:
            raise StudioError("Transcript cut needs keep-ranges")
        head = c["start"]
        items = []
        last = c["source_in"]
        for r in ranges:
            a, b = ticks(r["start"]), ticks(r["end"])
            if a < last or b <= a or b > c["source_out"]:
                raise StudioError("Transcript ranges must be ordered inside source")
            item = deepcopy(c)
            item.update(id=uid(), source_in=a, source_out=b, start=head, keyframes={})
            head += clip_duration(item)
            last = b
            items.append(item)
        tr["clips"].remove(c)
        tr["clips"].extend(items)
        if cmd.get("ripple", True):
            _shift(tr, c["start"]+clip_duration(c), head-c["start"]-clip_duration(c), [x["id"] for x in items])
        changed.extend([c["id"], *[x["id"] for x in items]])
    elif kind == "multicam.sync":
        # Offsets are measured by available timecode/audio analysis upstream;
        # this command never invents synchronization evidence.
        offsets = cmd["offsets"]
        if not isinstance(offsets, dict) or not offsets or not cmd.get("method") in ("timecode", "audio_correlation", "manual"):
            raise StudioError("Synchronization needs explicit offsets and method")
        for key, offset in offsets.items():
            _, _, c = _clip(p, key)
            c["start"] = ticks(offset)
            c["sync"] = {"method": cmd["method"], "evidence": cmd.get("evidence", {})}
            changed.append(key)
    else:
        raise StudioError(f"Unsupported command: {kind}")
    validate_project(p)
    return p, list(dict.fromkeys(changed)), warnings
