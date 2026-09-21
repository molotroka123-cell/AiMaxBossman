import { api, listOf } from './api.js';
import { toast, toastOk } from './components.js';

/*
 * Natural-language front door for BOSSMAN Images.
 *
 * The command bar should feel like an assistant, not a form switcher: explicit
 * image requests are routed into the native image queue before they become a
 * generic agent task.  The detector is deliberately conservative so "analyze
 * this image" or "find images" is not mistaken for generation.
 */

const RU_GENERATE = /\b(сгенерируй|нарисуй|создай|сделай)\b[\s\S]{0,42}\b(картинк\w*|изображени\w*|фото\w*|постер\w*|обложк\w*|рендер\w*)\b/i;
const RU_REVERSED = /\b(картинк\w*|изображени\w*|фото\w*|постер\w*|обложк\w*|рендер\w*)\b[\s\S]{0,42}\b(сгенерируй|нарисуй|создай|сделай)\b/i;
const EN_GENERATE = /\b(generate|create|draw|render|make)\b[\s\S]{0,36}\b(image|picture|photo|poster|cover|artwork|visual)\b/i;
const ANALYSIS_ONLY = /\b(проанализируй|опиши|что на|найди|поиск|analy[sz]e|describe|what(?:'s| is) in|find|search)\b/i;

export function isImageGenerationRequest(text) {
  const value = String(text || '').trim();
  if (!value) return false;
  if (ANALYSIS_ONLY.test(value) && !RU_GENERATE.test(value) && !RU_REVERSED.test(value) && !EN_GENERATE.test(value)) {
    return false;
  }
  return RU_GENERATE.test(value) || RU_REVERSED.test(value) || EN_GENERATE.test(value);
}

export function inferImageSpec(text) {
  const value = String(text || '');
  let aspect_ratio = '1:1';
  let width = 1024;
  let height = 1024;

  if (/\b(9\s*[:x×]\s*16|вертикал\w*|сторис|story|portrait)\b/i.test(value)) {
    aspect_ratio = '9:16'; width = 1024; height = 1792;
  } else if (/\b(16\s*[:x×]\s*9|горизонтал\w*|wide|landscape|баннер)\b/i.test(value)) {
    aspect_ratio = '16:9'; width = 1792; height = 1024;
  } else if (/\b(4\s*[:x×]\s*3)\b/i.test(value)) {
    aspect_ratio = '4:3'; width = 1536; height = 1152;
  }

  const precise = /\b(точн\w*|максимальн\w*|детал\w*|реалистич\w*|фотореал\w*|лицо|identity|preserve|precise|detailed|photoreal)\b/i.test(value);
  const high = /\b(ультра|максимальн\w*|high quality|highest quality|4k|8k)\b/i.test(value);

  return {
    aspect_ratio, width, height,
    prefer_precise: precise,
    options: { quality: high ? 'high' : 'auto', output_format: 'png', background: 'auto' },
  };
}

function pickExecutableModel(models, preferPrecise) {
  const real = (models || []).filter((m) => m && m.executable !== false && !(m.caps && m.caps.mock));
  const byAlias = new Map(real.map((m) => [m.alias, m]));
  if (preferPrecise && byAlias.has('openai-image-precise')) return 'openai-image-precise';
  if (byAlias.has('openai-image-fast')) return 'openai-image-fast';
  if (byAlias.has('openai-image-precise')) return 'openai-image-precise';
  if (byAlias.has('comfyui')) return 'comfyui';
  return real[0]?.alias || '';
}

export async function routeImageRequest(text, ctx) {
  if (!isImageGenerationRequest(text)) return false;

  const spec = inferImageSpec(text);
  const raw = await api.raw('/api/images/models');
  const models = listOf(raw);
  const model_alias = pickExecutableModel(models, spec.prefer_precise);

  if (!model_alias) {
    toast('Генератор изображений не настроен', {
      type: 'warn',
      hint: 'Добавьте OPENAI_API_KEY или запустите локальный ComfyUI.',
    });
    ctx.navigate('images');
    return true;
  }

  await api.raw('/api/images/jobs', {
    method: 'POST',
    body: {
      prompt: String(text).trim(),
      model_alias,
      aspect_ratio: spec.aspect_ratio,
      width: spec.width,
      height: spec.height,
      count: 1,
      kind: 'generate',
      tags: ['chat'],
      reference_asset_ids: [],
      options: spec.options,
    },
  });

  toastOk(`Картинка запущена · ${model_alias}`);
  ctx.navigate('images');
  return true;
}
