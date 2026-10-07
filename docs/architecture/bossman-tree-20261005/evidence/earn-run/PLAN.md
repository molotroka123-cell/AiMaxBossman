**Purpose**
Bossman is an autonomous agent platform that searches, processes, and acts on the internet to produce value (and money) for its owner. It operates as a careful analyst, leveraging local hardware and skills to identify and execute profitable opportunities without relying on cloud LLMs for reasoning.

**Ranked Options Table**

| Option | Expected $/Month | Effort | AMD Feasibility | Source URLs |
| :--- | :--- | :--- | :--- | :--- |
| **1. Local AI Art Service (ComfyUI)** | $200 - $800 | Medium | **High.** ROCm 7.2.1 natively supports ComfyUI on Windows 11 with Ryzen AI Max+ (gfx1151). 94GB unified memory allows large model inference. | https://rocm.blogs.amd.com/artificial-intelligence/comfyui-windows/README.html |
| **2. Telegram AI Bot (Subscription)** | $100 - $500 | High (Setup) | **High.** Uses local Ollama models (Qwen3.6 35B-A3B, etc.) for low-latency, zero-cost inference. No GPU needed for LLMs. | Local Ollama setup |
| **3. 3D Printing Service (Etsy/Fiverr)** | $100 - $400 | Medium | **N/A (Hardware).** Uses Elegoo Neptune 4 Pro. High demand for custom parts/miniatures. | Local Hardware |
| **4. Freelance AI Art Generation** | $50 - $300 | Medium | **High.** Same ComfyUI setup. Sell on Fiverr/Upwork. Rates: $15-$35/hr for AI artists. | https://www.upwork.com/hire/ai-generated-art-specialists |
| **5. GPU Rental (Salad/Vast.ai)** | $0 - $50 | Low | **Low/None.** Salad supports AMD for *mining* but AI workloads typically require NVIDIA/CUDA. Vast.ai AMD support is limited/enterprise-focused. | https://support.salad.com/faq/compatibility/is-my-machine-compatible-with-salad |
| **6. Local LLM API Service** | $0 - $100 | High | **Medium.** Can serve local LLMs via API, but market is saturated by free/open models. Hard to monetize directly. | Local Ollama setup |

**Rejected Options**

*   **GPU Rental (Vast.ai/Salad for AI):** REJECTED: Salad primarily supports NVIDIA GPUs for AI workloads. AMD support is limited to mining or specific enterprise cases. Vast.ai AMD support is not well-documented for consumer Ryzen AI iGPUs. High effort for low/no return.
*   **Cloud LLM Thinking:** REJECTED: Budget is $0. Cloud LLMs are not allowed for thinking.
*   **Fake Engagement/Botting:** REJECTED: Illegal and deceptive.

**First 3 Actions**

1.  **Set up ComfyUI with ROCm:** Verify the local ComfyUI installation with ROCm 7.2.1 on Windows 11. Generate a portfolio of 10-20 high-quality images using SDXL/Flux models to demonstrate capability. Use the 94GB unified memory to run larger models if needed.
2.  **Create Telegram AI Bot:** Build a simple Telegram bot using the local Ollama models (Qwen3.6 35B-A3B) for text generation. Offer it as a free trial to a small group to gather feedback and set up a subscription model for premium features (e.g., faster response, custom prompts).
3.  **List 3D Printing Services:** Create listings on Etsy and Fiverr for 3D printing services using the Elegoo Neptune 4 Pro. Focus on niche markets (e.g., custom miniatures, replacement parts) where local production offers a speed advantage over mass-market sellers.
