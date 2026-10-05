# Jeff 1.8: CosyVoice 3 candidate, license and consent checklist

CosyVoice 3 is a CANDIDATE next to Piper (`bcc/pit/tts_engines.py`). This checklist is the gate
between "files on the host" and "Jeff may use it". Nothing in this repository downloads or installs
the engine or its weights; every item below is for the owner to verify at the source. Statements
about upstream licenses are deliberately not made here: they must be checked against the exact
release you install.

## A. Before installing anything (owner)

1. Source is the upstream project's official release, pinned to a version and hash you recorded.
2. The code license of that release permits your use (personal / commercial, redistribution).
3. The weights license of the exact checkpoint permits your use. Code and weights can differ.
4. Any dataset or voice-prompt terms bundled with the weights are read and accepted.
5. The install lives outside the Git checkout (data dir or tools dir), not in the repository.

## B. Voice and consent

1. The reference/voice prompt (if the engine needs one) is your own voice or a voice you hold a
   written license for. A third party's voice is never imitated: no public figure, no participant,
   no acquaintance, no "sounds like".
2. Nobody's recording is used as a reference without their explicit, revocable consent on record.
3. Jeff speaks as Jeff. Synthetic speech is not presented as a real person's voice.
4. In Telegram the candidate speaks only in the owner's own chat; guests always get Piper (the same
   rule as the existing Chatterbox clone). The Jeff window on the owner's loopback host may use it.

## C. Turn the slot on (all three, in this order)

1. Write the consent file (outside the repository) with every item set to `true` only after A and B
   are done:

   ```json
   {"weights_license_checked": true,
    "voice_prompt_is_own_or_licensed": true,
    "no_third_party_voice_imitation": true}
   ```

2. Set `BOSSMAN_JEFF_COSYVOICE_CONSENT` to that file, `BOSSMAN_JEFF_COSYVOICE_CMD` to the local
   command (contract: `<cmd> --text-file <utf-8 txt> --out <mono PCM16 wav>`), and
   `BOSSMAN_JEFF_TTS_COSYVOICE=1`.
3. Check status: `/api/jeff/health` -> `heartbeat.tts` / `voice.tts.candidate` shows
   `available: true`, `reason_code: null`. Reason codes: `CANDIDATE_FLAG_OFF`,
   `CANDIDATE_NOT_INSTALLED`, `CANDIDATE_CONSENT_MISSING`, `CANDIDATE_CONSENT_INVALID`,
   `CANDIDATE_CONSENT_INCOMPLETE`, `VOICE_ENGINE_UNAVAILABLE`.

## D. Compare, then decide

1. `python -m bcc.pit.voice_bench --out voice-bench.json` on the owner host (same phrase set for
   every installed engine: stress, numbers, questions, pauses).
2. Select the candidate inside Jeff: `BOSSMAN_JEFF_TTS_ENGINE=cosyvoice`. A failing candidate falls
   back to Piper automatically.
3. Listen. WER and latency are not naturalness. The candidate is ADOPTED only after synthesis
   inside Jeff and the owner's listening, recorded by the owner. Until then it stays a candidate and
   Piper stays the default.

## E. Back out

Unset `BOSSMAN_JEFF_TTS_ENGINE` (or set it to `piper`) or set `BOSSMAN_JEFF_TTS_COSYVOICE=0`. No
data migration; Piper is unchanged.
