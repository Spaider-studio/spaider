"""Provider-neutral generation layer (0.4): one internal event stream for every
model source (bundled llama.cpp, own servers, cloud providers, signed-in
accounts).

Modules
  events        GenerationEvent / GenerationRequest / errors (the only contract
                ChatService, agent planner and UI depend on)
  sse           robust SSE / NDJSON line reader (fragmented UTF-8, heartbeats)
  base          ProviderAdapter interface + helpers
  chat_completions  OpenAI-compatible chat completions (llama.cpp, DeepSeek,
                Z.ai, Mistral, OpenRouter, Ollama, LM Studio, HF router, Gemini
                compatibility layer, any own server)
  responses     OpenAI Responses API (API key and ChatGPT sign-in)
  anthropic     Anthropic Messages API
  presets       provider presets (protocol, base URL, auth methods, capability
                defaults, parameter allowlists)
  connections   ProviderConnection records (no secrets) + capability profiles
  credentials   host-bound (DPAPI) and portable (Argon2id + AES-GCM) secrets
  registry      builds adapters for a connection, runs connection tests
"""
