#!/usr/bin/env bash
set -euo pipefail

if [[ -f /app/.env ]]; then
  set -a
  source /app/.env
  set +a
fi

if [[ -z "${LLM_PROVIDER:-}" ]]; then
  echo "[warn] LLM_PROVIDER unset; defaulting to anthropic" >&2
  export LLM_PROVIDER=anthropic
fi

case "${LLM_PROVIDER}" in
  anthropic)
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] || { echo "[error] ANTHROPIC_API_KEY missing" >&2; exit 1; }
    ;;
  openai)
    [[ -n "${OPENAI_API_KEY:-}" ]] || { echo "[error] OPENAI_API_KEY missing" >&2; exit 1; }
    ;;
  google)
    [[ -n "${GOOGLE_API_KEY:-}" ]] || { echo "[error] GOOGLE_API_KEY missing" >&2; exit 1; }
    ;;
  *)
    echo "[error] unknown LLM_PROVIDER=${LLM_PROVIDER}" >&2
    exit 1
    ;;
esac

exec "$@"
