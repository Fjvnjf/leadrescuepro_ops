# OpenAI / ChatGPT API Image Generation from Linux CLI (2026)

## 1. OpenAI Images API (DALL-E 3) from Terminal/curl

### Exact Endpoint
```
POST https://api.openai.com/v1/images/generations
```

### Authentication
- Header: `Authorization: Bearer $OPENAI_API_KEY`
- Content-Type: `application/json`
- API keys are managed at: https://platform.openai.com/api-keys

### DALL-E 3 Pricing (2026)
| Model      | Quality       | Resolution     | Price per image |
|------------|---------------|----------------|-----------------|
| DALL-E 3   | Standard      | 1024×1024      | $0.040          |
| DALL-E 3   | Standard      | 1024×1792 / 1792×1024 | $0.080  |
| DALL-E 3   | HD            | 1024×1024      | $0.080          |
| DALL-E 3   | HD            | 1024×1792 / 1792×1024 | $0.120  |
| DALL-E 2   | —             | 1024×1024      | $0.020          |
| DALL-E 2   | —             | 512×512        | $0.018          |
| DALL-E 2   | —             | 256×256        | $0.016          |

Note: DALL-E 3 only supports 1024×1024, 1024×1792, and 1792×1024.
It does NOT support arbitrary sizes like 1080×1080. You'd need to resize
post-generation with ImageMagick or similar.

### Supported sizes per model
- DALL-E 3: 1024×1024, 1792×1024, 1024×1792
- DALL-E 2: 256×256, 512×512, 1024×1024

---

## 2. ChatGPT Pro ($200/month) vs API Credits

### Short Answer: NO — Pro subscription does NOT include API credits.

OpenAI has two completely separate product lines:

### ChatGPT Subscriptions (Chat Interface)
- ChatGPT Plus: $20/month — access to GPT-4o, DALL-E via chat UI, limited messages
- ChatGPT Pro: $200/month — unlimited GPT-4o, extended DALL-E in chat, Advanced Voice, o1 pro mode, etc.
- These give you the CHAT INTERFACE at chatgpt.com only.

### OpenAI API (Developer Platform)
- Pay-as-you-go, billed per-token or per-image
- Completely separate billing at platform.openai.com
- You add pre-paid credits or set up monthly billing
- API usage is NOT covered by any ChatGPT subscription tier

### Can you use an API key from the same account?
YES. You can log into platform.openai.com with the same email/password
as your ChatGPT account. But you must:
1. Go to platform.openai.com → Settings → Billing
2. Add a payment method and buy credits (or enable post-pay billing)
3. Generate an API key at platform.openai.com/api-keys

The API key will work regardless of your ChatGPT subscription status,
but it draws from SEPARATE credits.

---

## 3. Bash/curl Command — Exact Syntax

### Prerequisites
```bash
# Install jq and ImageMagick (optional, for processing)
sudo apt-get install jq imagemagick curl
```

### Set your API key (get from platform.openai.com/api-keys)
```bash
export OPENAI_API_KEY="sk-your-key-here"
```

### Generate an image (DALL-E 3, standard quality)
```bash
curl -s https://api.openai.com/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "dall-e-3",
    "prompt": "A professional social media graphic for a real estate business, modern and clean design, blue and white color scheme with a house icon",
    "n": 1,
    "size": "1024x1024",
    "quality": "standard",
    "style": "vivid"
  }' | jq .
```

### Response format
```json
{
  "created": 1748572800,
  "data": [
    {
      "revised_prompt": "A polished, professional social media graphic...",
      "url": "https://oaidalleapiprodscus.blob.core.windows.net/private/org-.../img-....png"
    }
  ]
}
```

The `url` field contains a temporary download link (valid ~1 hour).

### Download the generated image automatically
```bash
# One-liner: generate + download
curl -s https://api.openai.com/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "dall-e-3",
    "prompt": "A clean modern social media post graphic for a business",
    "n": 1,
    "size": "1024x1024"
  }' | jq -r '.data[0].url' | xargs curl -o generated_image.png
```

### Resize to 1080x1080 (since DALL-E only does 1024)
```bash
# After downloading the 1024x1024 image:
convert generated_image.png -resize 1080x1080 social_post_1080.png
```

### Full production-ready bash script
```bash
#!/bin/bash
# generate_social_image.sh — Generate and download a DALL-E image for social media
set -euo pipefail

API_KEY="${OPENAI_API_KEY:?Set OPENAI_API_KEY environment variable}"
PROMPT="${1:?Usage: $0 'your image prompt here' [output_filename]}"
OUTPUT="${2:-social_image.png}"
RESIZE="${3:-1080x1080}"

echo "Generating image for prompt: $PROMPT"

# Call DALL-E 3
RESPONSE=$(curl -s https://api.openai.com/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d "$(jq -n --arg prompt "$PROMPT" '{
    model: "dall-e-3",
    prompt: $prompt,
    n: 1,
    size: "1024x1024",
    quality: "standard"
  }')")

# Extract image URL
IMAGE_URL=$(echo "$RESPONSE" | jq -r '.data[0].url')

if [ "$IMAGE_URL" = "null" ] || [ -z "$IMAGE_URL" ]; then
    echo "ERROR: Failed to generate image"
    echo "$RESPONSE" | jq .
    exit 1
fi

echo "Downloading from: $IMAGE_URL"
curl -s -o "${OUTPUT}" "$IMAGE_URL"

# Resize if needed (1080x1080 vs 1024x1024)
if [ "$RESIZE" != "1024x1024" ]; then
    echo "Resizing to $RESIZE..."
    convert "${OUTPUT}" -resize "$RESIZE" "${OUTPUT}"
fi

echo "Done: ${OUTPUT}"
file "${OUTPUT}"
```

---

## 4. Cheapest Way to Generate Social Media Images (1080×1080) Programmatically

### Option A: DALL-E 2 via API — ~$0.02/image
```bash
curl https://api.openai.com/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{"model": "dall-e-2", "prompt": "...", "n": 1, "size": "1024x1024"}'
```
- $0.020/image at 1024×1024
- Then resize to 1080×1080 with ImageMagick
- Cheapest OpenAI option

### Option B: Stability AI API (Stable Diffusion) — ~$0.003/image
- Endpoint: `https://api.stability.ai/v2beta/stable-image/generate/sd3`
- Pricing: ~$0.003–$0.01 per image (credit-based)
- Supports arbitrary sizes including 1080×1080 natively
```bash
curl -f -sS "https://api.stability.ai/v2beta/stable-image/generate/sd3" \
  -H "authorization: Bearer $STABILITY_KEY" \
  -H "accept: image/*" \
  -F "prompt=A clean social media graphic..." \
  -F "output_format=png" \
  -F "aspect_ratio=1:1" \
  -o output.png
```

### Option C: Replicate.com — various models from ~$0.001/image
- FLUX.1-schnell: ~$0.001–$0.003/image (fast, solid quality)
- SDXL: ~$0.001/image
- Access via REST API with curl

### Option D: Run local Stable Diffusion (FREE, needs GPU)
- Install Automatic1111 or ComfyUI on a machine with GPU
- Or use llama.cpp-style quantized models for CPU-only (slower)
- Free per-image, but requires hardware and setup

### Option E: Hugging Face Inference API — free tier available
- Some models have free inference
- Rate-limited but zero cost for low volume
```bash
curl https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"inputs": "A social media graphic..."}' \
  -o output.png
```

### Cost Comparison Summary
| Service              | Model            | ~Cost/Image | 1080×1080? |
|---------------------|------------------|-------------|------------|
| OpenAI              | DALL-E 3 Std     | $0.040      | Resize needed |
| OpenAI              | DALL-E 2         | $0.020      | Resize needed |
| Stability AI        | SD3              | ~$0.003     | Native ✓   |
| Replicate           | FLUX.1-schnell   | ~$0.001     | Native ✓   |
| Hugging Face        | FLUX.1-schnell   | Free tier   | Native ✓   |
| Self-hosted         | SDXL/FLUX        | $0.00       | Native ✓   |

### Key Limitation: DALL-E 3 does NOT support 1080×1080
You must generate at 1024×1024 and upscale/resize. This is a real limitation
for social media where 1080×1080 is the standard Instagram/Facebook square size.
The 56-pixel difference is small enough that resizing with ImageMagick (Lanczos)
produces acceptable results.

---

## Quick Reference: Generate a free test image

Using Hugging Face's free inference API (no credit card needed):
```bash
# Get free token at https://huggingface.co/settings/tokens
export HF_TOKEN="hf_your_token_here"

curl -s https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"inputs": "Professional real estate social media post, modern clean design"}' \
  -o test_social_image.png

file test_social_image.png
# FLUX.1-schnell generates at 1024x1024 by default, resize with:
convert test_social_image.png -resize 1080x1080! test_social_1080.png
```
