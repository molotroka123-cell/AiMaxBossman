# Network and the Tapo C200

## Topology

```
Tapo C200  ->  clinic private LAN
           ->  Tailscale/WireGuard subnet router
           ->  encrypted tunnel
           ->  home server running ai-webcam-vision
```

Never port-forward RTSP (554) or ONVIF to the public Internet. The service
binds `127.0.0.1` by default; if you change `AWV_HOST`, set `AWV_API_TOKEN` as
well.

## Camera account

Create a dedicated **Camera Account** in the Tapo app for RTSP/ONVIF. Do not
reuse the TP-Link cloud account password. Put the credentials in the
environment (`AWV_CAMERA_USERNAME`, `AWV_CAMERA_PASSWORD`) — never in a command
line, a config file in the repository, or a URL you paste anywhere.

Stream paths:

```
rtsp://<user>:<password>@<ip>:554/stream1   # high resolution, for later bursts
rtsp://<user>:<password>@<ip>:554/stream2   # default here: low bitrate sampling
```

`AWV_CAMERA_STREAM` accepts only `stream1` or `stream2`.

## Motion

Order of preference:

1. an ONVIF motion/event source, **if** the exact firmware exposes a usable
   subscription. No ONVIF client exists in this build and none has been run
   against Tapo C200 firmware: `capabilities.motion.onvif_subscription`
   reports `implemented: false`, `verified_on_tapo_c200: false`,
   `evidence: NOT RUN`. Do not read the plan as a feature;
2. a clinic edge/NVR bridge;
3. a low-rate frame-difference fallback.

Whichever produces the event, translate it into `POST /hooks/motion` with a
`source` label. Do **not** scrape iOS/Android push notifications.

Status of the frame-difference fallback: not implemented in this version.
The motion gate is driven by the webhook; without a webhook the service
samples at the idle interval. This is stated here rather than implied to be
working.

The webhook is the vendor-neutral path and the only one that is proven:
`tests/test_motion_ingress.py` drives `/hooks/motion` with arbitrary vendor
labels, with no label at all, and with an attacker-supplied camera address
that the hook must ignore (it is a wake signal, never a transport).

## PTZ

The C200 pans and tilts. Baseline/zone analysis assumes a fixed pose: disable
patrol and auto-tracking while analytics runs. A moved camera invalidates the
baseline — recapture it (`jobs.create {"type":"baseline"}`) after any
repositioning. A future version can keep named PTZ presets with a baseline per
preset.

## Verification status

There is no physical Tapo C200 in the build environment. Everything up to the
camera — process spawn, URL assembly, timeouts, kill-on-timeout, error
classification, redaction, reconnect — is exercised against real ffmpeg with
generated fixtures and a refused RTSP endpoint. The physical camera path is
**BLOCKED BY HARDWARE** and must be verified on site with:

```bash
AWV_CAMERA_MODE=rtsp AWV_CAMERA_HOST=<ip> AWV_CAMERA_USERNAME=<user> \
AWV_CAMERA_PASSWORD=<password> ai-webcam-vision check
# then: POST /api/v1/jobs {"type":"probe"} and {"type":"baseline"}
```
