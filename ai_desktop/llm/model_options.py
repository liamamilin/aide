"""One source for the global Ollama generation options."""
from ai_desktop import config


def global_options():
    return {
        'num_predict': config.OLLAMA_NUM_PREDICT,
        'num_ctx': config.OLLAMA_NUM_CTX,
        'temperature': config.OLLAMA_TEMPERATURE,
        'top_p': config.OLLAMA_TOP_P,
        'top_k': config.OLLAMA_TOP_K,
        'repeat_penalty': config.OLLAMA_REPEAT_PENALTY,
    }
