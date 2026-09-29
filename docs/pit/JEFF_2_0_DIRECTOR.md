# Jeff 2.0 module: director (order 30)

File: `command-center/bcc/pit/j2/director.py`. Tests: `command-center/tests/test_jeff_2_director.py`.

The director decides how Jeff should answer this turn. It adds notes only (data for the model) and may trim trailing
questions in `post_reply`. It never blocks a turn and never grants anything.

## Intent and dialogue act

`classify(text)` is a pure, deterministic rule engine with 22 intents: greeting, farewell, thanks, smalltalk, question,
howto, task, code, advice, emotional, feedback_negative, feedback_positive, correction, continue, confirm, deny, meta,
current, translate, math, roleplay, unclear. Each intent maps to a dialogue act (`social`, `ask`, `ask_advice`, `request`,
`share`, `feedback`, `answer`, `unclear`). Specific patterns outrank the generic question shape ("сколько будет 2+3?" is
maths, "какая погода?" needs live data).

Model second: when rule confidence is below 0.62 and a local chat callable is injected (`runtime.j2_local_chat`, or the local
adapter when `BOSSMAN_JEFF_J2_DIRECTOR_MODEL=on`), one short classification is requested with a 0.3 s cap. The answer must
contain a known label; anything else falls back to the rules. The user text is framed as data. Without a callable the module is
rules-only, which is the default (no extra GPU load per turn).

Accuracy: the labelled Russian set in the test file has 128 utterances covering every intent; the test asserts overall
accuracy of at least 0.88 and recall of at least 0.6 per intent. The set was used while tuning the rules, so real-world accuracy
will be lower; the model-second path exists for that gap.

## One clarifying question

A plan carries a question only for a missing detail in a task, translation, code or advice request ("Напиши письмо",
"Переведи", "Посоветуй фильм", "Помоги мне"). It is never asked on consecutive turns, at most twice in six turns, never on
emotional, social, feedback or how-to turns, never for a follow-up, and never when the participant says "не спрашивай" or
"без вопросов". The note names the single question and forbids others; `post_reply` drops extra trailing questions beyond one
and strips boilerplate closers ("Чем ещё могу помочь?"). Questions inside the body are never touched.

## Topic state

Per participant: keyword stems with scores and decay (0.6 per turn), at most 6 stems, follow-up detection ("а почему оно так?"),
topic-shift detection (new content words with no overlap resets the topic), last intent, recent ask turns. In memory (LRU 500).
When the participant enabled memory, the state is also written to `<person_dir>/j2/director.json` inside that participant's own
namespace: stems and counters only, never message text. Turning memory off deletes the file at the next turn. An invalid person
key never touches the filesystem.

## Response plan

Length (`one_line`, `short`, `medium`, `long`) and shape (`direct`, `steps`, `options`, `code`, `empathy_first`,
`question_first`, `conversational`, `continue`) come from the intent, the size of the message and explicit wishes ("кратко",
"в двух словах", "подробно", "пошагово"). Current-events turns add a reminder not to invent live data. Notes are under 420
characters so the pipeline budget (1800) is shared fairly with other modules.

## Status keys

`turns`, `by_intent`, `rule_decisions`, `model_decisions`, `model_failures`, `clarifications`, `clarification_skipped`,
`follow_ups`, `topic_shifts`, `tracked_participants`, `persisted`, `model_assist`.

`BOSSMAN_JEFF_J2_DIRECTOR=off` disables this module alone.
