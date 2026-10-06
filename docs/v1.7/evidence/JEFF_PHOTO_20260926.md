# Jeff photo generation — owner-PC evidence, 2026-09-26

Status: **image generation and Telegram delivery observed; text chat blocked by expired OpenRouter key**. This is a live capability check, not a full 1.7 release acceptance.

- Local Studio used `sdcpp:z-image-turbo` on the owner PC. The [original](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) and [GGUF](https://huggingface.co/leejet/Z-Image-Turbo-GGUF) model cards state Apache-2.0. No paid image provider was used.
- Studio job 1 generated a 302,444-byte PNG and Telegram `sendPhoto` returned a message ID for the owner. Integrated Jeff `ParticipantRuntime._generate_image` then generated a 1,271,645-byte, 1024×1024 PNG through persisted PIT media configuration and returned success only after Telegram accepted the photo. Output and receipts are outside Git at `C:\Users\asd\AppData\Local\Bossman\owner-run\jeff-photo-001`.
- A truthful HTML-formatted capability notice was delivered once to all five known active private participants, with five Telegram message IDs. It stated that ordinary text chat was temporarily unavailable.
- Two real inbound Telegram image requests entered PIT, were interrupted by an unexpected worker restart, and completed in Studio. Each result was matched to its original request and delivered to that private chat, then the inbox rows were marked done conditionally. Recovery receipts are stored in `interrupted-delivery-recovery.jsonl` outside Git. Automatic restart reconciliation remains under review.
- Targeted PIT suite: **70 passed** after the image path and advisory telemetry access fixes. PIT was running with an empty queue at the final check.

## Open limits

- OpenRouter authentication returns HTTP 401 for the saved key. Jeff must keep the cloud-only chat route unavailable until a valid owner-provided key passes authenticated doctor. No local chat substitution is allowed by the owner request.
- Vision and editing are not configured by this generation-only media path.
- Windows denies this user access to two advisory behavior files and one resource log. The runtime now degrades advisory scoring without resetting participant consent or memory; those inaccessible files were not read or deleted.
- The reason for the unexpected PIT restart was not identified at this checkpoint. The end-to-end photo receipts do not establish restart-safe automatic delivery.
