# UI-TARS visual grounding

This optional integration adds UI-TARS grounding to the existing Bossman core
Computer Operator. It does not launch the separate UI-TARS Desktop application.

The existing local planner first selects a single typed action, named target and
expected postcondition. For CLICK and DOUBLE_CLICK only, UI-TARS locates that
target in the current screenshot. Its coordinates enter the same operator
manager, policy, approval, owner pause/stop, freshness and post-state checks.
Other action kinds continue through the existing planner and adapters.

Native `finished()` output cannot complete a Bossman task. Arbitrary Python,
multiple actions, unsupported operations, out-of-bounds coordinates and action
kind disagreements are rejected. No `eval`, `exec` or generated pyautogui script
is used. If the model returns `wait()` because the target is ambiguous, the
grounding attempt fails safely and the existing bounded replan loop handles it.

## Configure

1. Install the existing Bossman Windows desktop dependencies and run the normal
   Computer Operator first. This integration requires its real desktop backend,
   screenshots, owner permissions and a working structured local planner.
2. Deploy a compatible UI-TARS-1.5/Qwen2.5-VL visual model through an existing
   **local** Bossman model gateway route. Configure the route's model, endpoint,
   credentials and access for the existing `computer-operator` agent using the
   gateway's normal configuration. A text-only GGUF or unsupported visual
   projector is not sufficient. No model is downloaded by this integration.
3. Before starting the **core Computer Operator process**, set:

   ```powershell
   $env:BOSSMAN_COMPUTER_PLANNER = 'uitars'
   $env:BOSSMAN_UITARS_MODEL = 'your-existing-local-visual-model-alias'
   ```

   The existing structured planner alias remains unchanged. Both calls use
   `planner_chat` and `llm.chat`, preserving the gateway and its `cloud_policy=never`.
   Screenshots must never silently fall back to a cloud model. Setting an alias
   here does not create a gateway route or grant that route permission.
4. Restart the core process. The OSS inventory's `configured` state means only
   that both environment values are present in the Command Center process.
   If core and Command Center are separate processes, set consistent values in
   both. Configuration does not establish model availability or successful use.

## Coordinate contract

The adapter supports the upstream literal forms
`click(start_box='(x,y)')`, `left_double(start_box='(x,y)')` and equivalent
`point='<point>x y</point>'` forms. Coordinates are pixels in the model input,
not normalized 0..1000. The screenshot is resized to Qwen2.5-VL's factor of 28,
sent at those dimensions, and the returned point is scaled back to the capture.
The model server must preserve those input dimensions during preprocessing.
Other UI-TARS versions and coordinate conventions require a separate verified
adapter; do not silently reuse this mapping.

## Acceptance

Test single and double clicks on a harmless local application, missing targets,
different Windows scaling settings, pause/stop, observe-only mode and a denied
action. Verify actual post-state and confirm screenshots stay local. Physical
Windows, model correctness, DPI behavior and Radeon acceleration were not
tested in the source-contract suite. This integration makes no model-quality or
hardware-performance claim.

Official sources:
- <https://github.com/bytedance/UI-TARS>
- <https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md>
- <https://github.com/bytedance/UI-TARS/blob/main/codes/ui_tars/prompt.py>

Tests: `bossman-core/tests/test_uitars_grounding.py`.
