// Mock routes of API contract Addendum v1.3 (0.4): providers, connections,
// credentials (vault password "test1234"), projects + consents, conversation
// bindings, and the model library (discover / repo / card). Registered from
// server.mjs; returns the helpers the chat stream needs (policy, sharing).
//
// Simulations: connection test -> network error when the base URL contains
// "down", 401 for the API key "bad", success otherwise; a connection whose
// name contains "leer" lists no models; repositories of meta-llama are gated.
import { randomUUID } from "node:crypto";

const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

const CAP = (over = {}) => ({ text: "declared", vision: "unknown", tools: "unknown", structured_output: "unknown", streaming: "declared", reasoning: "unknown", notes_de: "", ...over });

export const PRESETS = [
  { id: "llama_local", name: "Eingebettetes llama.cpp (Standard)", protocol: "llama_local", base_url: "", base_url_editable: false, auth_methods: ["none"], execution: "local", capabilities: CAP({ text: "verified", vision: "verified", tools: "verified", structured_output: "verified", streaming: "verified", reasoning: "declared", notes_de: "Fähigkeiten je Modell laut Katalog; Messung über Kalibrierung." }), key_url: "", docs_url: "", subscription_note_de: "", data_note_de: "Verarbeitung ausschließlich auf diesem Rechner.", free_access_check: true },
  { id: "openai_compatible", name: "Eigener Server (OpenAI-kompatibel)", protocol: "chat_completions", base_url: "", base_url_editable: true, auth_methods: ["none", "api_key"], execution: "unknown", capabilities: CAP({ notes_de: "Fähigkeiten hängen vom Server ab; Verbindungstest prüft Erreichbarkeit, Zugang, Modellliste und eine Probe-Antwort." }), key_url: "", docs_url: "", subscription_note_de: "", data_note_de: "Ausführungsort laut deiner Angabe (lokal, LAN oder Cloud). Ein lokal erreichbarer Server kann selbst an eine Cloud weiterleiten.", free_access_check: true },
  { id: "ollama", name: "Ollama (bereits laufend)", protocol: "chat_completions", base_url: "http://127.0.0.1:11434/v1", base_url_editable: true, auth_methods: ["none", "api_key"], execution: "local", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared", notes_de: "Modelle mit Suffix ':cloud' werden in der Ollama-Cloud verarbeitet." }), key_url: "", docs_url: "https://docs.ollama.com/api/openai-compatibility", subscription_note_de: "", data_note_de: "Lokale Modelle laufen auf diesem Rechner; Cloud-Modelle (':cloud') nicht.", free_access_check: true },
  { id: "lmstudio", name: "LM Studio (bereits laufend)", protocol: "chat_completions", base_url: "http://127.0.0.1:1234/v1", base_url_editable: true, auth_methods: ["none", "api_key"], execution: "local", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared" }), key_url: "", docs_url: "https://lmstudio.ai/docs/developer/openai-compat", subscription_note_de: "", data_note_de: "Verarbeitung durch LM Studio auf diesem Rechner.", free_access_check: true },
  { id: "openai", name: "OpenAI (eigener API-Schlüssel)", protocol: "responses", base_url: "https://api.openai.com/v1", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared", reasoning: "declared" }), key_url: "https://platform.openai.com/api-keys", docs_url: "https://developers.openai.com/api/docs/guides/streaming-responses", subscription_note_de: "Ein ChatGPT-Abo ist kein API-Schlüssel. Für die Nutzung eines ChatGPT-Plans gibt es die getrennte Karte „ChatGPT-Konto (Sign in with ChatGPT)“.", data_note_de: "Anfragen gehen an OpenAI; Abrechnung über den API-Schlüssel.", free_access_check: true },
  { id: "openai_siwc", name: "ChatGPT-Konto (Sign in with ChatGPT, optional)", protocol: "responses", base_url: "https://api.openai.com/v1", base_url_editable: false, auth_methods: ["oauth_siwc"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", notes_de: "Vorschau laut OpenAI-Dokumentation: store=false, Limits des Plans." }), key_url: "", docs_url: "https://developers.openai.com/siwc/token-sharing-open-source", subscription_note_de: "Nutzt den ChatGPT-Plan des angemeldeten Kontos für Responses-Anfragen; kein Zugriff auf ChatGPT-Gespräche; Limits des Plans gelten; kein stiller Wechsel auf API-Key-Abrechnung.", data_note_de: "Anfragen gehen an OpenAI unter dem angemeldeten ChatGPT-Konto.", free_access_check: true },
  { id: "anthropic", name: "Anthropic Claude (eigener API-Schlüssel)", protocol: "anthropic_messages", base_url: "https://api.anthropic.com/v1", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", reasoning: "declared" }), key_url: "https://console.anthropic.com/settings/keys", docs_url: "https://platform.claude.com/docs/en/build-with-claude/streaming", subscription_note_de: "Ein Claude-Abo (claude.ai) ist kein API-Zugang; es gibt keinen offiziell dokumentierten Abo-Zugang für Drittanwendungen – daher nur API-Schlüssel.", data_note_de: "Anfragen gehen an Anthropic.", free_access_check: true },
  { id: "gemini", name: "Google Gemini (eigener API-Schlüssel, Kompatibilitätsschicht)", protocol: "chat_completions", base_url: "https://generativelanguage.googleapis.com/v1beta/openai", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared", reasoning: "declared", notes_de: "OpenAI-Kompatibilitätsschicht (Beta): Thinking nicht abschaltbar." }), key_url: "https://aistudio.google.com/apikey", docs_url: "https://aistudio.google.com/docs/openai", subscription_note_de: "Ein Gemini-App-Abo ist kein API-Zugang.", data_note_de: "Anfragen gehen an Google.", free_access_check: true },
  { id: "deepseek", name: "DeepSeek API", protocol: "chat_completions", base_url: "https://api.deepseek.com", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "no", tools: "declared", structured_output: "declared", reasoning: "declared", notes_de: "Thinking standardmäßig an; mit Werkzeugen muss reasoning_content zurückgegeben werden." }), key_url: "https://platform.deepseek.com/api_keys", docs_url: "https://api-docs.deepseek.com/guides/thinking_mode/", subscription_note_de: "", data_note_de: "Anfragen gehen an DeepSeek.", free_access_check: true },
  { id: "zai", name: "Z.ai / GLM (Standard-API)", protocol: "chat_completions", base_url: "https://api.z.ai/api/paas/v4", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ tools: "declared" }), key_url: "https://z.ai/manage-apikey/apikey-list", docs_url: "https://docs.z.ai/guides/overview/quick-start", subscription_note_de: "Der GLM Coding Plan nutzt einen gesonderten Endpunkt und wird hier nicht als Ersatz der Standard-API verwendet.", data_note_de: "Anfragen gehen an Z.ai.", free_access_check: true },
  { id: "mistral", name: "Mistral API", protocol: "chat_completions", base_url: "https://api.mistral.ai/v1", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared" }), key_url: "https://console.mistral.ai/api-keys", docs_url: "https://docs.mistral.ai/inference/conversations/chat-completion", subscription_note_de: "", data_note_de: "Anfragen gehen an Mistral.", free_access_check: true },
  { id: "openrouter", name: "OpenRouter (optionaler Aggregator)", protocol: "chat_completions", base_url: "https://openrouter.ai/api/v1", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ vision: "declared", tools: "declared", structured_output: "declared", notes_de: "Routing-Regeln (provider.order/only/allow_fallbacks/data_collection) über extra.provider." }), key_url: "https://openrouter.ai/keys", docs_url: "https://openrouter.ai/docs/features/provider-routing", subscription_note_de: "", data_note_de: "OpenRouter leitet an den gewählten Anbieter weiter; Datenrichtlinie über data_collection=deny einschränkbar.", free_access_check: true },
  { id: "huggingface", name: "Hugging Face Inference Providers (optional)", protocol: "chat_completions", base_url: "https://router.huggingface.co/v1", base_url_editable: false, auth_methods: ["api_key"], execution: "cloud", capabilities: CAP({ tools: "declared", structured_output: "declared", notes_de: "Modellliste des Routers nennt ausführenden Provider, Kontext, Preise, supports_tools." }), key_url: "https://huggingface.co/settings/tokens", docs_url: "https://huggingface.co/docs/inference-providers/hub-api", subscription_note_de: "Ein HF-Token für Downloads ist eine andere Verbindung als die Inferenz über den Router.", data_note_de: "Anfragen gehen an den jeweils ausführenden Inference Provider.", free_access_check: true },
];

const NEXT_ACTION_DE = {
  out_of_memory: "Modell mit weniger GPU-Schichten oder auf CPU neu laden (Einstellungen → Modelle), Kontext verkleinern oder ein kleineres Modell wählen.",
  empty_answer: "Erneut senden; bleibt die Antwort leer, Modell oder Verbindung prüfen (Verbindungstest unter Verbindungen).",
  rate_limited: "Kurz warten und erneut senden; Limits des Anbieters beachten.",
  unauthorized: "Schlüssel unter Verbindungen prüfen oder neu hinterlegen.",
  forbidden: "Berechtigung/Richtlinie des Anbieters prüfen; nicht wiederholen.",
  network: "Netzverbindung und Serveradresse prüfen; Verbindungstest ausführen.",
  timeout: "Erneut versuchen; bei eigenen Servern Erreichbarkeit prüfen.",
  consent_required: "Freigabe im Dialog erteilen oder ein lokales Modell wählen.",
  local_only: "Projekt auf „Cloud erlaubt“ umstellen oder ein lokales Modell wählen.",
  denied: "Datenfreigabe der Verbindung ändern oder ein lokales Modell wählen.",
  disabled: "Verbindung unter Einstellungen → Verbindungen aktivieren oder ein lokales Modell wählen.",
  unknown_connection: "Ein anderes Modell oder eine andere Verbindung wählen.",
  runtime_not_ready: "Modell unter Einstellungen → Modelle aktivieren.",
  generation_failed: "Erneut versuchen; bleibt der Fehler, Protokoll unter Einstellungen → Info prüfen.",
};

const MODEL_LISTS = {
  ollama: [
    { id: "llama3.2:3b", display_name: "Llama 3.2 3B", owned_by: "ollama", context_length: 131072, declared: { text: true, vision: false, tools: true } },
    { id: "qwen3:8b", display_name: "Qwen3 8B", owned_by: "ollama", context_length: 40960, declared: { text: true, vision: false, tools: true, structured_output: true } },
    { id: "gpt-oss:120b-cloud", display_name: "gpt-oss 120B (Ollama-Cloud)", owned_by: "ollama", context_length: 131072, declared: { text: true, tools: true } },
  ],
  lmstudio: [
    { id: "qwen2.5-7b-instruct", display_name: "Qwen2.5 7B Instruct", owned_by: "lmstudio", context_length: 32768, declared: { text: true, tools: true } },
    { id: "gemma-3-4b-it", display_name: "Gemma 3 4B", owned_by: "lmstudio", context_length: 8192, declared: { text: true, vision: true } },
  ],
  openai: [
    { id: "gpt-5.1", display_name: "GPT-5.1", owned_by: "openai", context_length: 400000, declared: { text: true, vision: true, tools: true, structured_output: true } },
    { id: "gpt-5-mini", display_name: "GPT-5 mini", owned_by: "openai", context_length: 400000, declared: { text: true, vision: true, tools: true, structured_output: true } },
    { id: "gpt-4.1", display_name: "GPT-4.1", owned_by: "openai", context_length: 1047576, declared: { text: true, vision: true, tools: true, structured_output: true } },
  ],
  anthropic: [
    { id: "claude-sonnet-4-5", display_name: "Claude Sonnet 4.5", owned_by: "anthropic", context_length: 200000, declared: { text: true, vision: true, tools: true } },
    { id: "claude-haiku-4-5", display_name: "Claude Haiku 4.5", owned_by: "anthropic", context_length: 200000, declared: { text: true, vision: true, tools: true } },
  ],
  gemini: [
    { id: "gemini-2.5-flash", display_name: "Gemini 2.5 Flash", owned_by: "google", context_length: 1048576, declared: { text: true, vision: true, tools: true, structured_output: true } },
    { id: "gemini-2.5-pro", display_name: "Gemini 2.5 Pro", owned_by: "google", context_length: 1048576, declared: { text: true, vision: true, tools: true, structured_output: true } },
  ],
  deepseek: [
    { id: "deepseek-chat", display_name: "DeepSeek V3.2 (chat)", owned_by: "deepseek", context_length: 131072, declared: { text: true, vision: false, tools: true, structured_output: true }, verified: { text: "verified", vision: "no", tools: "verified", structured_output: "declared" } },
    { id: "deepseek-reasoner", display_name: "DeepSeek V3.2 (reasoner)", owned_by: "deepseek", context_length: 131072, declared: { text: true, vision: false, tools: true } },
  ],
  zai: [
    { id: "glm-4.6", display_name: "GLM-4.6", owned_by: "zai", context_length: 200000, declared: { text: true, tools: true } },
    { id: "glm-4.5-air", display_name: "GLM-4.5 Air", owned_by: "zai", context_length: 128000, declared: { text: true, tools: true } },
  ],
  mistral: [
    { id: "mistral-large-latest", display_name: "Mistral Large", owned_by: "mistralai", context_length: 131072, declared: { text: true, tools: true, structured_output: true } },
    { id: "mistral-small-latest", display_name: "Mistral Small", owned_by: "mistralai", context_length: 131072, declared: { text: true, vision: true, tools: true, structured_output: true } },
  ],
  openrouter: [
    { id: "openai/gpt-5-mini", display_name: "GPT-5 mini (via OpenRouter)", owned_by: "openai", context_length: 400000, declared: { text: true, vision: true, tools: true } },
    { id: "qwen/qwen3-235b-a22b", display_name: "Qwen3 235B A22B", owned_by: "qwen", context_length: 40960, declared: { text: true, tools: true } },
  ],
  huggingface: [
    { id: "Qwen/Qwen3-32B:cerebras", display_name: "Qwen3 32B (cerebras)", owned_by: "cerebras", context_length: 32768, declared: { text: true, tools: true } },
    { id: "deepseek-ai/DeepSeek-V3.2:novita", display_name: "DeepSeek V3.2 (novita)", owned_by: "novita", context_length: 131072, declared: { text: true, tools: true } },
  ],
  openai_compatible: [{ id: "local-model", display_name: "local-model", owned_by: "server", context_length: 8192, declared: { text: true } }],
  openai_siwc: [{ id: "gpt-5.1", display_name: "GPT-5.1 (ChatGPT-Konto)", owned_by: "openai", context_length: 400000, declared: { text: true, vision: true, tools: true } }],
};

// ---- discover data (deterministic) ------------------------------------------------------
const VERIFIED = { Qwen: "offiziell (Alibaba Qwen)", "zai-org": "offiziell (Z.ai / GLM)", "deepseek-ai": "offiziell (DeepSeek)", mistralai: "offiziell (Mistral AI)", "meta-llama": "offiziell (Meta Llama)", google: "offiziell (Google)", microsoft: "offiziell (Microsoft)", "ggml-org": "offiziell (ggml / llama.cpp)" };
const REPO_SPECS = [
  ["Qwen", "Qwen3-4B-GGUF", "4B"], ["Qwen", "Qwen3-8B-GGUF", "8B"], ["Qwen", "Qwen3-30B-A3B-GGUF", "30B"], ["Qwen", "Qwen3-VL-4B-Instruct-GGUF", "4B", "vl"], ["Qwen", "Qwen3-VL-8B-Instruct-GGUF", "8B", "vl"], ["Qwen", "Qwen3-235B-A22B-GGUF", "235B"],
  ["zai-org", "GLM-4.6-GGUF", "355B"], ["zai-org", "GLM-4.5-Air-GGUF", "106B"], ["zai-org", "GLM-4.1V-9B-Thinking-GGUF", "9B", "vl"],
  ["deepseek-ai", "DeepSeek-R1-Distill-Qwen-7B", "7B", "nogguf"], ["deepseek-ai", "DeepSeek-V3.2-GGUF", "671B"],
  ["mistralai", "Mistral-Small-3.2-24B-Instruct-2506-GGUF", "24B"], ["mistralai", "Ministral-3-3B-Instruct-GGUF", "3B"], ["mistralai", "Devstral-Small-2-GGUF", "24B"],
  ["google", "gemma-3-4b-it-GGUF", "4B", "gated-auto", "vl"], ["google", "gemma-4-E2B-it-GGUF", "2B", "gated-auto", "vl"], ["google", "gemma-3-27b-it-GGUF", "27B", "gated-auto"],
  ["microsoft", "Phi-4-mini-instruct-GGUF", "4B"], ["microsoft", "Phi-4-GGUF", "14B"],
  ["meta-llama", "Llama-3.2-3B-Instruct", "3B", "gated-manual", "nogguf"], ["meta-llama", "Llama-3.1-8B-Instruct-GGUF", "8B", "gated-manual"],
  ["unsloth", "Qwen3-4B-GGUF", "4B"], ["unsloth", "Qwen3-8B-GGUF", "8B"], ["unsloth", "gemma-3-4b-it-GGUF", "4B", "vl"], ["unsloth", "DeepSeek-R1-0528-Qwen3-8B-GGUF", "8B"], ["unsloth", "Mistral-Small-3.2-24B-Instruct-2506-GGUF", "24B"], ["unsloth", "GLM-4.5-Air-GGUF", "106B"], ["unsloth", "Qwen3-30B-A3B-GGUF", "30B"],
  ["bartowski", "Qwen_Qwen3-4B-GGUF", "4B"], ["bartowski", "Qwen_Qwen3-8B-GGUF", "8B"], ["bartowski", "mistralai_Ministral-3-3B-Instruct-GGUF", "3B"], ["bartowski", "microsoft_Phi-4-mini-instruct-GGUF", "4B"], ["bartowski", "zai-org_GLM-4.6-GGUF", "355B"], ["bartowski", "google_gemma-3-12b-it-GGUF", "12B", "vl"],
  ["TheBloke", "Mistral-7B-Instruct-v0.2-GGUF", "7B"], ["TheBloke", "Llama-2-13B-chat-GGUF", "13B"], ["TheBloke", "CodeLlama-34B-Instruct-GGUF", "34B"],
  ["ggml-org", "Qwen3-VL-4B-Instruct-GGUF", "4B", "vl"], ["ggml-org", "gemma-3-4b-it-GGUF", "4B", "vl"], ["ggml-org", "SmolVLM-500M-Instruct-GGUF", "0.5B", "vl"],
  ["lmstudio-community", "Qwen3-4B-GGUF", "4B"], ["lmstudio-community", "Qwen3-8B-GGUF", "8B"], ["lmstudio-community", "Mistral-Small-3.2-24B-Instruct-2506-GGUF", "24B"], ["lmstudio-community", "gemma-3-4b-it-GGUF", "4B", "vl"],
  ["Qwen", "Qwen2.5-7B-Instruct-GGUF", "7B"], ["Qwen", "Qwen2.5-Coder-7B-Instruct-GGUF", "7B"], ["Qwen", "Qwen3-Embedding-0.6B-GGUF", "0.6B"], ["mistralai", "Magistral-Small-2509-GGUF", "24B"],
];

function hash(s) {
  let h = 2166136261;
  for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
  return h;
}
function paramsToBytes(p) {
  const n = parseFloat(p);
  return Math.round(n * 0.62 * GB); // ~Q4_K_M bytes per billion parameters
}
const DISCOVER_ITEMS = REPO_SPECS.map(([author, name, params, ...flags]) => {
  const h = hash(`${author}/${name}`);
  const vl = flags.includes("vl");
  const gguf = !flags.includes("nogguf");
  const gated = flags.includes("gated-manual") ? "manual" : flags.includes("gated-auto") ? "auto" : false;
  const days = h % 320;
  return {
    repo: `${author}/${name}`,
    author,
    name,
    publisher_status: VERIFIED[author] ?? "unbekannt / nicht verifiziert",
    likes: 50 + (h % 4200),
    downloads: 1200 + ((h >> 3) % 980000),
    updated: new Date(Date.UTC(2026, 10, 11) - days * 86400000).toISOString(),
    gguf,
    gated,
    private: false,
    pipeline_tag: vl ? "image-text-to-text" : "text-generation",
    library: gguf ? "gguf" : "transformers",
    family_hints: [/qwen3/i.test(name) ? (vl ? "qwen3_vl" : "qwen3") : /glm/i.test(name) ? "glm4" : /gemma-4/i.test(name) ? "gemma4" : /gemma/i.test(name) ? "gemma3" : /llama/i.test(name) ? "llama" : /mistral|ministral|devstral|magistral/i.test(name) ? "mistral" : /phi/i.test(name) ? "phi3" : /deepseek/i.test(name) ? "deepseek_v3" : "conversational"],
    status: "unverified",
    _params: params,
    _vl: vl,
  };
});

function discoverPage({ q, author, task, gguf_only, sort, limit, cursor }) {
  const needle = q.trim().toLowerCase();
  let items = DISCOVER_ITEMS.filter((i) => !needle || i.repo.toLowerCase().includes(needle) || i.family_hints.some((f) => f.includes(needle)));
  if (author.trim()) items = items.filter((i) => i.author.toLowerCase() === author.trim().toLowerCase());
  if (task !== "any") items = items.filter((i) => i.pipeline_tag === task);
  if (gguf_only) items = items.filter((i) => i.gguf);
  const key = { downloads: (i) => i.downloads, likes: (i) => i.likes, updated: (i) => Date.parse(i.updated), trending: (i) => i.likes * 3 + i.downloads / 1000 }[sort] ?? ((i) => i.downloads);
  items = [...items].sort((a, b) => key(b) - key(a));
  const offset = Math.max(0, parseInt(cursor || "0", 10) || 0);
  const page = items.slice(offset, offset + limit).map(({ _params, _vl, ...rest }) => rest);
  const next = offset + limit < items.length ? String(offset + limit) : null;
  return { items: page, next_cursor: next, total: items.length };
}

function repoDetail(repo, installedRepos) {
  const item = DISCOVER_ITEMS.find((i) => i.repo === repo);
  if (!item) return null;
  if (!item.gguf) return { repo, revision: "main", author: item.author, publisher_status: item.publisher_status, gated: item.gated, license: "apache-2.0", base_model: null, tags: ["transformers", "safetensors"], last_modified: item.updated, variants: [], total_gguf_bytes: 0, installed: false };
  const base = paramsToBytes(item._params);
  const stem = item.name.replace(/-GGUF$/i, "");
  const quants = [
    ["Q4_K_M", 1.0],
    ["Q5_K_M", 1.18],
    ["Q8_0", 1.72],
    ["F16", 3.3],
  ];
  const variants = quants.map(([quant, factor]) => {
    const bytes = Math.round(base * factor);
    const parts = bytes > 8 * GB ? Math.ceil(bytes / (8 * GB)) : 1;
    const files = Array.from({ length: parts }, (_, idx) => ({
      path: parts > 1 ? `${stem}-${quant}-${String(idx + 1).padStart(5, "0")}-of-${String(parts).padStart(5, "0")}.gguf` : `${stem}-${quant}.gguf`,
      size: Math.round(bytes / parts),
      sha256: idx === 0 ? hash(`${repo}${quant}`).toString(16).padStart(8, "0").repeat(8) : null,
      split_index: parts > 1 ? idx + 1 : null,
      split_total: parts > 1 ? parts : null,
    }));
    const ram = 32 * GB;
    return {
      key: quant,
      quantization: quant,
      kind: "weights",
      files,
      bytes,
      parts,
      needs_projector: item._vl,
      projector_options: item._vl ? ["mmproj-F16", "mmproj-Q8_0"] : [],
      fit: bytes > ram * 0.85 ? "too_large" : bytes > ram * 0.55 ? "tight" : "ok",
      fit_note_de: "Schätzung nur aus der Dateigröße gegen den Arbeitsspeicher; der Katalogeintrag rechnet genauer (Kontext, KV-Cache).",
    };
  });
  if (item._vl) {
    variants.push(
      { key: "mmproj-F16", quantization: "F16", kind: "projector", files: [{ path: `mmproj-${stem}-F16.gguf`, size: 840 * MB, sha256: "ab".repeat(32), split_index: null, split_total: null }], bytes: 840 * MB, parts: 1, needs_projector: false, projector_options: [], fit: null },
      { key: "mmproj-Q8_0", quantization: "Q8_0", kind: "projector", files: [{ path: `mmproj-${stem}-Q8_0.gguf`, size: 455 * MB, sha256: "cd".repeat(32), split_index: null, split_total: null }], bytes: 455 * MB, parts: 1, needs_projector: false, projector_options: [], fit: null },
    );
  }
  if (/Qwen3-8B|GLM-4.5/i.test(item.name)) {
    variants.push({ key: "draft-0.6B", quantization: "Q8_0", kind: "auxiliary", files: [{ path: `${stem}-draft-0.6B-Q8_0.gguf`, size: 640 * MB, sha256: null, split_index: null, split_total: null }], bytes: 640 * MB, parts: 1, needs_projector: false, projector_options: [], fit: null });
  }
  return {
    repo,
    revision: hash(repo).toString(16).padStart(8, "0").repeat(5),
    author: item.author,
    publisher_status: item.publisher_status,
    gated: item.gated,
    license: /gemma/i.test(item.name) ? "gemma" : /llama/i.test(item.name) ? "llama3.1" : "apache-2.0",
    base_model: `${item.author}/${stem}`,
    tags: ["gguf", item.pipeline_tag, ...item.family_hints, "conversational"],
    last_modified: item.updated,
    variants,
    total_gguf_bytes: variants.reduce((a, v) => a + v.bytes, 0),
    installed: installedRepos.has(repo),
  };
}

function modelCard(repo) {
  const item = DISCOVER_ITEMS.find((i) => i.repo === repo);
  if (!item) return null;
  const stem = item.name.replace(/-GGUF$/i, "");
  const excerpt = [
    `# ${stem}`,
    "",
    `GGUF-Quantisierungen von **${stem}** für llama.cpp. Dieser Text ist eine **Mock-Modellkarte** (kein echter Inhalt); HTML wird nicht gerendert: <script>alert(1)</script>`,
    "",
    "## Varianten",
    "",
    "| Quantisierung | Hinweis |",
    "|---|---|",
    "| Q4_K_M | guter Kompromiss |",
    "| Q8_0 | nahezu verlustfrei |",
    "",
    "## Nutzung",
    "",
    "```bash",
    `llama-server -m ${stem}-Q4_K_M.gguf --jinja -c 8192`,
    "```",
    "",
    `Lizenz und Details: [Modellseite](https://huggingface.co/${repo}).`,
  ].join("\n");
  return { repo, excerpt, front_matter: { license: /gemma/i.test(item.name) ? "gemma" : "apache-2.0", base_model: `${item.author}/${stem}`, pipeline_tag: item.pipeline_tag, library_name: "gguf" }, truncated: false, url: `https://huggingface.co/${repo}` };
}

// ---- registration -------------------------------------------------------------------------
export function registerModelHub({ route, json, error, state, now, offline = false }) {
  const hub = {
    connections: new Map(),
    keys: new Map(), // connection id -> { value, storage }
    consents: [],
    projects: new Map(),
    vault: { exists: false, unlocked: false, password: null, items: 0, path: "D:\\SpAIder\\data\\vault.json" },
    sessionItems: 0,
    hostAvailable: true,
  };
  state.hub = hub;

  const project = (id, name, local_only) => ({ id, name, local_only, created_at: now(), updated_at: now() });
  hub.projects.set("default", project("default", "Allgemein", false));
  hub.projects.set("proj_vertraulich", project("proj_vertraulich", "Vertraulich", true));
  hub.projects.set("proj_nordlicht", project("proj_nordlicht", "Projekt Nordlicht", false));

  const presetOf = (id) => PRESETS.find((p) => p.id === id) ?? null;
  const connView = (c) => ({ ...c, has_credential: hub.keys.has(c.id) });
  const credentialStore = () => ({
    host_available: hub.hostAvailable,
    vault: { exists: hub.vault.exists, unlocked: hub.vault.unlocked, path: hub.vault.path, kdf: hub.vault.exists ? { name: "argon2id", memory_kib: 65536, iterations: 3, lanes: 1 } : null },
    session_items: hub.sessionItems,
  });
  const makeConnection = (fields) => {
    const preset = presetOf(fields.provider);
    const id = `conn_${randomUUID().slice(0, 10)}`;
    const conn = {
      id,
      provider: fields.provider,
      protocol: preset.protocol,
      name: fields.name,
      base_url: fields.base_url ?? preset.base_url,
      auth_method: fields.auth_method,
      credential_storage: fields.auth_method === "api_key" ? (fields.credential_storage ?? "host") : "none",
      account_label: fields.account_label ?? null,
      workspace: null,
      execution: fields.execution ?? preset.execution,
      data_sharing: fields.data_sharing ?? "ask",
      default_model: fields.default_model ?? null,
      extra: fields.extra ?? {},
      enabled: true,
      last_test: null,
      created_at: now(),
      updated_at: now(),
    };
    hub.connections.set(id, conn);
    if (fields.api_key) {
      hub.keys.set(id, { value: fields.api_key, storage: conn.credential_storage });
      if (conn.credential_storage === "session") hub.sessionItems += 1;
      if (conn.credential_storage === "vault") hub.vault.items += 1;
    }
    return conn;
  };
  // Seeded connections: a local Ollama (no key, allowed) and a cloud DeepSeek (host key, asks).
  makeConnection({ provider: "ollama", name: "Ollama lokal", auth_method: "none", execution: "local", data_sharing: "allow", default_model: "qwen3:8b" });
  makeConnection({ provider: "deepseek", name: "DeepSeek", auth_method: "api_key", api_key: "sk-mock", credential_storage: "host", data_sharing: "ask", default_model: "deepseek-chat" });

  const projectsList = () =>
    [...hub.projects.values()]
      .sort((a, b) => (a.id === "default" ? -1 : b.id === "default" ? 1 : a.name.localeCompare(b.name)))
      .map((p) => ({ ...p, conversations: [...state.conversations.values()].filter((c) => (c.project_id ?? "default") === p.id).length, documents: 0 }));

  const keyProblem = (conn) => {
    // returns an error code when the key cannot be read (vault locked / host-bound elsewhere)
    if (conn.auth_method !== "api_key") return null;
    const key = hub.keys.get(conn.id);
    if (!key) return "credential";
    if (key.storage === "vault" && !hub.vault.unlocked) return "locked";
    return null;
  };
  const modelsFor = (conn) => {
    if (/leer|empty/i.test(conn.name)) return [];
    const list = MODEL_LISTS[conn.provider] ?? MODEL_LISTS.openai_compatible;
    return list.map((m) => ({
      id: m.id,
      display_name: m.display_name,
      owned_by: m.owned_by,
      context_length: m.context_length,
      capabilities: m.verified ? { scope: `connection:${conn.id}`, model: m.id, text: m.verified.text, vision: m.verified.vision, tools: m.verified.tools, structured_output: m.verified.structured_output, streaming: "verified", reasoning: "unknown", context_length: m.context_length, parameters: { temperature: "verified" }, notes_de: "Durch Verbindungstest geprüft.", checked_at: now() } : null,
      declared: m.declared,
    }));
  };
  const simulateTest = (conn, probeModel, allowGeneration) => {
    const t0 = Date.now();
    const latency = 40 + (hash(conn.id) % 260);
    const key = hub.keys.get(conn.id);
    const res = { server_reached: false, access_ok: null, model_listed: null, answer_ok: null, models: [], message_de: "", error: null, latency_ms: latency, details: {} };
    if (/down/i.test(conn.base_url)) {
      res.message_de = "Server nicht erreichbar.";
      res.error = { code: "network", message_de: `Verbindung zu ${conn.base_url} fehlgeschlagen (ECONNREFUSED).`, status: null, retryable: true, retry_after_s: null };
      return res;
    }
    res.server_reached = true;
    if (conn.auth_method === "api_key" && (!key || key.value === "bad")) {
      res.access_ok = false;
      res.message_de = "Zugang abgelehnt.";
      res.error = { code: "unauthorized", message_de: key ? "401 Unauthorized – der Schlüssel wurde abgelehnt." : "Kein Schlüssel hinterlegt.", status: key ? 401 : null, retryable: false, retry_after_s: null };
      return res;
    }
    res.access_ok = true;
    const models = modelsFor(conn);
    res.models = models.map((m) => m.id);
    res.model_listed = probeModel ? res.models.includes(probeModel) : models.length > 0;
    res.message_de = `Server erreicht, Zugang geprüft, ${models.length} Modell(e) gelistet.`;
    if (!res.model_listed) res.message_de += probeModel ? ` Modell ${probeModel} nicht in der Liste.` : " Keine Modelle gelistet.";
    if (allowGeneration) {
      res.answer_ok = true;
      res.details.probe_answer = "OK";
      res.message_de += " Probe-Antwort erhalten.";
    }
    res.details.elapsed_ms = Date.now() - t0;
    return res;
  };

  // ---- policy (mirrors providers/policy.py) --------------------------------------------
  const decide = (conv) => {
    const binding = conv.binding ?? { kind: "local", connection_id: null, provider_model_id: null, local_model_id: state.activeChat };
    const proj = hub.projects.get(conv.project_id ?? "default") ?? hub.projects.get("default");
    if (binding.kind !== "connection") return { binding, project: proj, decision: { allowed: true, reason_de: "", needs_consent: false, code: "ok" }, connection: null };
    const conn = hub.connections.get(binding.connection_id);
    if (!conn) return { binding, project: proj, decision: { allowed: false, reason_de: "Die gewählte Verbindung existiert nicht mehr.", needs_consent: false, code: "unknown_connection" }, connection: null };
    if (!conn.enabled) return { binding, project: proj, decision: { allowed: false, reason_de: `Die Verbindung „${conn.name}“ ist deaktiviert.`, needs_consent: false, code: "disabled" }, connection: conn };
    if (proj.local_only && conn.execution !== "local") return { binding, project: proj, decision: { allowed: false, reason_de: `Das Projekt „${proj.name}“ ist als „Nur lokal“ markiert; „${conn.name}“ verarbeitet außerhalb dieses Rechners (${conn.execution}).`, needs_consent: false, code: "local_only" }, connection: conn };
    if (conn.data_sharing === "deny") return { binding, project: proj, decision: { allowed: false, reason_de: `Für „${conn.name}“ ist die Datenfreigabe auf „nie“ gesetzt.`, needs_consent: false, code: "denied" }, connection: conn };
    if (conn.data_sharing === "allow" || conn.execution === "local") return { binding, project: proj, decision: { allowed: true, reason_de: "", needs_consent: false, code: "ok" }, connection: conn };
    if (hub.consents.some((c) => c.connection_id === conn.id && c.project_id === proj.id)) return { binding, project: proj, decision: { allowed: true, reason_de: "", needs_consent: false, code: "ok" }, connection: conn };
    return { binding, project: proj, decision: { allowed: false, reason_de: `Bevor Inhalte des Projekts „${proj.name}“ an „${conn.name}“ (${conn.execution}) gesendet werden, ist eine einmalige Freigabe nötig.`, needs_consent: true, code: "consent_required" }, connection: conn };
  };
  const bindingResponse = (conv) => {
    const r = decide(conv);
    const projectRow = projectsList().find((p) => p.id === r.project.id) ?? r.project;
    return { binding: r.binding, project: projectRow, policy: r.decision, connection: r.connection ? connView(r.connection) : null };
  };
  const sharingPreview = (conv, messages) => {
    const r = decide(conv);
    return {
      connection_id: r.connection?.id ?? null,
      connection_name: r.connection?.name ?? "",
      execution: r.connection?.execution ?? "unknown",
      project_id: r.project.id,
      history_messages: messages.filter((m) => m.role === "user" || m.role === "assistant").length,
      system_prompt: true,
      memory_chars: 57,
      sources: 0,
      images: 0,
    };
  };

  // ---- routes: providers / connections -----------------------------------------------------
  route("GET", /^\/api\/providers$/, (ctx) => {
    const providers = PRESETS.map((p) => ({ ...p, connections: [...hub.connections.values()].filter((c) => c.provider === p.id).map((c) => c.id) }));
    json(ctx.res, 200, { providers, credential_store: credentialStore() });
  });
  route("GET", /^\/api\/connections$/, (ctx) => json(ctx.res, 200, { connections: [...hub.connections.values()].map(connView) }));
  route("POST", /^\/api\/connections$/, (ctx) => {
    const b = ctx.body ?? {};
    const preset = presetOf(String(b.provider ?? ""));
    if (!preset) return error(ctx.res, 404, "Unbekannter Anbieter.", "not_found");
    if (preset.id === "llama_local") return error(ctx.res, 409, "Die eingebettete Laufzeit braucht keine Verbindung.", "invalid");
    if (!preset.auth_methods.includes(b.auth_method)) return error(ctx.res, 422, `Zugangsmethode ${b.auth_method} ist für ${preset.name} nicht vorgesehen (erlaubt: ${preset.auth_methods.join(", ")}).`, "invalid_auth");
    const editable = preset.base_url_editable || preset.id === "openai_compatible";
    if (editable && !String(b.base_url ?? "").trim()) return error(ctx.res, 422, "Serveradresse fehlt.", "invalid");
    if (!editable && b.base_url && b.base_url !== preset.base_url) return error(ctx.res, 422, `Die Basis-URL von ${preset.name} ist festgelegt (${preset.base_url}); für eigene Server das Preset „Eigener Server“ verwenden.`, "invalid");
    if (b.auth_method === "api_key") {
      if (!String(b.api_key ?? "").trim()) return error(ctx.res, 422, "API-Schlüssel fehlt.", "invalid");
      if (b.credential_storage === "vault" && !hub.vault.unlocked) return error(ctx.res, 409, hub.vault.exists ? "Der Tresor ist gesperrt." : "Es gibt noch keinen Tresor.", "locked");
      if ((b.credential_storage ?? "host") === "host" && !hub.hostAvailable) return error(ctx.res, 409, "Host-gebundene Ablage (DPAPI) ist nicht verfügbar.", "unsupported");
    }
    const conn = makeConnection({ ...b, name: String(b.name ?? preset.name).trim() || preset.name, base_url: editable ? String(b.base_url).trim() : preset.base_url });
    json(ctx.res, 201, { connection: connView(conn) });
  });
  route("GET", /^\/api\/connections\/([^/]+)$/, (ctx) => {
    const c = hub.connections.get(ctx.params[0]);
    if (!c) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    json(ctx.res, 200, { connection: connView(c), capabilities: [] });
  });
  route("PATCH", /^\/api\/connections\/([^/]+)$/, (ctx) => {
    const c = hub.connections.get(ctx.params[0]);
    if (!c) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    const b = ctx.body ?? {};
    for (const k of ["name", "base_url", "auth_method", "account_label", "execution", "data_sharing", "default_model", "extra", "enabled"]) if (b[k] !== undefined) c[k] = b[k];
    if (b.api_key) {
      if ((b.credential_storage ?? c.credential_storage) === "vault" && !hub.vault.unlocked) return error(ctx.res, 409, "Der Tresor ist gesperrt.", "locked");
      c.credential_storage = b.credential_storage ?? (c.credential_storage === "none" ? "host" : c.credential_storage);
      hub.keys.set(c.id, { value: b.api_key, storage: c.credential_storage });
    } else if (b.credential_storage && hub.keys.has(c.id)) {
      c.credential_storage = b.credential_storage;
      hub.keys.get(c.id).storage = b.credential_storage;
    }
    if (c.auth_method !== "api_key") {
      hub.keys.delete(c.id);
      c.credential_storage = "none";
    }
    c.updated_at = now();
    json(ctx.res, 200, { connection: connView(c) });
  });
  route("DELETE", /^\/api\/connections\/([^/]+)$/, (ctx) => {
    const c = hub.connections.get(ctx.params[0]);
    if (!c) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    hub.connections.delete(c.id);
    hub.keys.delete(c.id);
    hub.consents = hub.consents.filter((x) => x.connection_id !== c.id);
    for (const conv of state.conversations.values()) if (conv.binding?.connection_id === c.id) conv.binding = null;
    json(ctx.res, 200, { ok: true });
  });
  route("POST", /^\/api\/connections\/([^/]+)\/test$/, (ctx) => {
    const c = hub.connections.get(ctx.params[0]);
    if (!c) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    if (keyProblem(c) === "locked") return error(ctx.res, 409, "Der Tresor ist gesperrt – bitte entsperren.", "locked");
    const b = ctx.body ?? {};
    setTimeout(() => {
      const result = simulateTest(c, b.probe_model ? String(b.probe_model) : null, Boolean(b.allow_generation));
      c.last_test = { at: now(), server_reached: result.server_reached, access_ok: result.access_ok, model_listed: result.model_listed, answer_ok: result.answer_ok, message_de: result.message_de, latency_ms: result.latency_ms };
      c.updated_at = now();
      json(ctx.res, 200, { result, connection: connView(c) });
    }, 350);
  });
  route("GET", /^\/api\/connections\/([^/]+)\/models$/, (ctx) => {
    const c = hub.connections.get(ctx.params[0]);
    if (!c) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    if (/down/i.test(c.base_url)) return error(ctx.res, 502, `Verbindung zu ${c.base_url} fehlgeschlagen (ECONNREFUSED).`, "network");
    const kp = keyProblem(c);
    if (kp === "locked") return error(ctx.res, 409, "Der Tresor ist gesperrt – bitte entsperren.", "locked");
    if (kp === "credential") return error(ctx.res, 409, "Kein Schlüssel hinterlegt.", "credential");
    if (hub.keys.get(c.id)?.value === "bad") return error(ctx.res, 409, "401 Unauthorized – der Schlüssel wurde abgelehnt.", "unauthorized");
    json(ctx.res, 200, { models: modelsFor(c) });
  });

  // ---- routes: credentials ---------------------------------------------------------------------
  route("GET", /^\/api\/credentials\/status$/, (ctx) => json(ctx.res, 200, credentialStore()));
  route("POST", /^\/api\/credentials\/vault\/create$/, (ctx) => {
    const pw = String(ctx.body?.password ?? "");
    if (hub.vault.exists) return error(ctx.res, 409, "Es gibt bereits einen Tresor.", "exists");
    if (pw.length < 8) return error(ctx.res, 422, "Passwort zu kurz (mindestens 8 Zeichen).", "invalid");
    hub.vault.exists = true;
    hub.vault.unlocked = true;
    hub.vault.password = pw;
    json(ctx.res, 200, { ...credentialStore(), items: 0, unlock_ms: 180 });
  });
  route("POST", /^\/api\/credentials\/vault\/unlock$/, (ctx) => {
    const pw = String(ctx.body?.password ?? "");
    if (!hub.vault.exists) return error(ctx.res, 409, "Es gibt noch keinen Tresor.", "vault_missing");
    if (pw !== (hub.vault.password ?? "test1234")) return error(ctx.res, 401, "Falsches Passwort.", "wrong_password");
    hub.vault.unlocked = true;
    json(ctx.res, 200, { ...credentialStore(), items: hub.vault.items, unlock_ms: 210 });
  });
  route("POST", /^\/api\/credentials\/vault\/lock$/, (ctx) => {
    hub.vault.unlocked = false;
    json(ctx.res, 200, credentialStore());
  });
  route("POST", /^\/api\/credentials\/vault\/change-password$/, (ctx) => {
    const pw = String(ctx.body?.password ?? "");
    const next = String(ctx.body?.new_password ?? "");
    if (!next) return error(ctx.res, 422, "Neues Passwort fehlt.", "invalid");
    if (pw !== (hub.vault.password ?? "test1234")) return error(ctx.res, 401, "Falsches Passwort.", "wrong_password");
    hub.vault.password = next;
    json(ctx.res, 200, credentialStore());
  });

  // ---- routes: projects + consents ------------------------------------------------------------
  route("GET", /^\/api\/projects$/, (ctx) => json(ctx.res, 200, { projects: projectsList(), consents: hub.consents }));
  route("POST", /^\/api\/projects$/, (ctx) => {
    const p = project(`proj_${randomUUID().slice(0, 8)}`, String(ctx.body?.name ?? "Projekt").trim().slice(0, 120), Boolean(ctx.body?.local_only));
    hub.projects.set(p.id, p);
    json(ctx.res, 201, { project: { ...p, conversations: 0, documents: 0 } });
  });
  route("PATCH", /^\/api\/projects\/([^/]+)$/, (ctx) => {
    const p = hub.projects.get(ctx.params[0]);
    if (!p) return error(ctx.res, 404, "Projekt nicht gefunden.", "not_found");
    if (typeof ctx.body?.name === "string") p.name = ctx.body.name.trim().slice(0, 120);
    if (typeof ctx.body?.local_only === "boolean") p.local_only = ctx.body.local_only;
    p.updated_at = now();
    json(ctx.res, 200, { project: projectsList().find((x) => x.id === p.id) });
  });
  route("DELETE", /^\/api\/projects\/([^/]+)$/, (ctx) => {
    if (ctx.params[0] === "default") return error(ctx.res, 409, "Das Standardprojekt kann nicht gelöscht werden.", "invalid");
    if (!hub.projects.delete(ctx.params[0])) return error(ctx.res, 404, "Projekt nicht gefunden.", "not_found");
    for (const conv of state.conversations.values()) if (conv.project_id === ctx.params[0]) conv.project_id = "default";
    hub.consents = hub.consents.filter((c) => c.project_id !== ctx.params[0]);
    json(ctx.res, 200, { ok: true });
  });
  route("POST", /^\/api\/consents$/, (ctx) => {
    const { connection_id, project_id } = ctx.body ?? {};
    if (!hub.connections.has(connection_id)) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
    const p = hub.projects.get(project_id);
    if (!p) return error(ctx.res, 404, "Projekt nicht gefunden.", "not_found");
    if (p.local_only) return error(ctx.res, 409, "Für ein „Nur lokal“-Projekt kann keine Freigabe erteilt werden.", "local_only");
    const consent = { connection_id, project_id, scope: ctx.body?.scope ?? "chat", granted_at: now() };
    hub.consents = [...hub.consents.filter((c) => !(c.connection_id === connection_id && c.project_id === project_id)), consent];
    json(ctx.res, 200, { consent });
  });
  route("DELETE", /^\/api\/consents\/([^/]+)\/([^/]+)$/, (ctx) => {
    const before = hub.consents.length;
    hub.consents = hub.consents.filter((c) => !(c.connection_id === ctx.params[0] && c.project_id === ctx.params[1]));
    json(ctx.res, 200, { ok: hub.consents.length < before });
  });

  // ---- routes: conversation binding / project ----------------------------------------------------
  route("GET", /^\/api\/conversations\/([^/]+)\/binding$/, (ctx) => {
    const conv = state.conversations.get(ctx.params[0]);
    if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
    json(ctx.res, 200, bindingResponse(conv));
  });
  route("PUT", /^\/api\/conversations\/([^/]+)\/binding$/, (ctx) => {
    const conv = state.conversations.get(ctx.params[0]);
    if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
    const b = ctx.body ?? {};
    if (b.kind === "connection") {
      if (!b.connection_id || !b.provider_model_id) return error(ctx.res, 422, "Verbindung und Modell-ID sind erforderlich.", "invalid");
      if (!hub.connections.has(b.connection_id)) return error(ctx.res, 404, "Verbindung nicht gefunden.", "not_found");
      conv.binding = { kind: "connection", connection_id: b.connection_id, provider_model_id: b.provider_model_id, local_model_id: null };
    } else {
      conv.binding = { kind: "local", connection_id: null, provider_model_id: null, local_model_id: state.activeChat };
    }
    conv.updated_at = now();
    json(ctx.res, 200, bindingResponse(conv));
  });
  route("PUT", /^\/api\/conversations\/([^/]+)\/project$/, (ctx) => {
    const conv = state.conversations.get(ctx.params[0]);
    if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
    const pid = String(ctx.body?.project_id ?? "");
    if (!hub.projects.has(pid)) return error(ctx.res, 404, "Projekt nicht gefunden.", "not_found");
    conv.project_id = pid;
    conv.updated_at = now();
    json(ctx.res, 200, { conversation: { ...conv, project_id: conv.project_id, binding: conv.binding ?? null, postprocessing_pending: 0 } });
  });

  // ---- routes: model library ----------------------------------------------------------------------
  route("GET", /^\/api\/models\/discover$/, (ctx) => {
    const q = ctx.url.searchParams;
    const query = { q: q.get("q") ?? "", author: q.get("author") ?? "", task: q.get("task") ?? "any", gguf_only: (q.get("gguf_only") ?? "true") !== "false", sort: q.get("sort") ?? "downloads" };
    const limit = Math.max(1, Math.min(50, parseInt(q.get("limit") ?? "20", 10) || 20));
    if (offline) return json(ctx.res, 200, { items: [], next_cursor: null, query, tasks: { "text-generation": "Text", "image-text-to-text": "Text + Bild", any: "Alle Aufgaben" }, sorts: ["downloads", "likes", "updated", "trending"], cached_at: null, offline: true, detail: "Keine Netzwerkverbindung." });
    if (/limit/i.test(query.q)) return error(ctx.res, 429, "Hugging Face begrenzt die Anfragen – kurz warten.", "rate_limited", { retry_after_s: 20 });
    const page = discoverPage({ ...query, limit, cursor: q.get("cursor") ?? "" });
    setTimeout(() => json(ctx.res, 200, { items: page.items, next_cursor: page.next_cursor, query, tasks: { "text-generation": "Text", "image-text-to-text": "Text + Bild", any: "Alle Aufgaben" }, sorts: ["downloads", "likes", "updated", "trending"], cached_at: now(), offline: false }), 250);
  });
  route("GET", /^\/api\/models\/repo\/([^/]+)\/([^/]+)$/, (ctx) => {
    const repo = `${ctx.params[0]}/${ctx.params[1]}`;
    if (offline) return error(ctx.res, 503, "Hugging Face ist nicht erreichbar.", "offline");
    if (/^meta-llama\//.test(repo)) return error(ctx.res, 409, `Das Repository ${repo} ist zugangsbeschränkt (gated).`, "gated", { requires_account: true });
    const installedRepos = new Set([...state.installed.values()].map((m) => m.repo).filter(Boolean));
    const detail = repoDetail(repo, installedRepos);
    if (!detail) return error(ctx.res, 404, `Repository ${repo} wurde auf Hugging Face nicht gefunden.`, "not_found");
    setTimeout(() => json(ctx.res, 200, { ...detail, cached_at: now(), offline: false }), 300);
  });
  route("GET", /^\/api\/models\/card\/([^/]+)\/([^/]+)$/, (ctx) => {
    const repo = `${ctx.params[0]}/${ctx.params[1]}`;
    if (offline) return error(ctx.res, 503, "Hugging Face ist nicht erreichbar.", "offline");
    if (/^meta-llama\//.test(repo)) return error(ctx.res, 409, "Die Modellkarte erfordert eine Freischaltung.", "gated", { requires_account: true });
    const card = modelCard(repo);
    if (!card) return error(ctx.res, 404, "Repository nicht gefunden.", "not_found");
    json(ctx.res, 200, { ...card, cached_at: now(), offline: false });
  });

  return { decide, bindingResponse, sharingPreview, NEXT_ACTION_DE, presetOf, connView, discoverItems: DISCOVER_ITEMS };
}
