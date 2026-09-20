"""The AI providers OmniReview can call.

Model IDs live in the ai_models catalog table; projects pin a catalog model, and administrators keep the catalog current
as providers rename or retire models. Set <PROVIDER>_BASE_URL (for example QWEN_BASE_URL) to use another regional
endpoint.
"""

import os
from dataclasses import dataclass
from typing import Literal

AdapterKind = Literal["anthropic", "gemini", "openai_compatible"]

# Every stored embedding has this many dimensions, so one pgvector column and index serve every embedding model.
# Only models that can produce vectors of this size belong in the catalog.
EMBEDDING_DIMENSIONS = 1024


def platform_keys_enabled() -> bool:
    """AI_PLATFORM_KEYS=false makes the server bring-your-own-key only: the server's provider keys are never used."""
    return os.getenv("AI_PLATFORM_KEYS", "true").strip().lower() != "false"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    adapter: AdapterKind
    api_key_env: str
    # Shown to users as a data-residency notice before they send review content to the provider.
    headquarters: str
    base_url: str | None = None
    # OpenAI's current models require max_completion_tokens; most OpenAI-compatible APIs accept only max_tokens.
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    # Texts per embeddings request, and whether the embeddings API accepts a requested vector size.
    embedding_batch_size: int = 64
    embedding_dimensions_param: bool = True

    @property
    def resolved_base_url(self) -> str | None:
        return os.getenv(f"{self.id.upper()}_BASE_URL") or self.base_url

    def platform_api_key(self) -> str | None:
        """The server-wide key from the environment, treating blanks and template placeholders as missing. None when
        the server uses only users' own keys."""
        if not platform_keys_enabled():
            return None
        value = os.getenv(self.api_key_env, "").strip()
        if not value or value.lower().startswith("your"):
            return None
        return value


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in [
        ProviderSpec(
            id="anthropic",
            label="Anthropic Claude",
            adapter="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            headquarters="Anthropic, United States",
        ),
        ProviderSpec(
            id="gemini",
            label="Google Gemini",
            adapter="gemini",
            api_key_env="GEMINI_API_KEY",
            headquarters="Google, United States",
            embedding_batch_size=100,
        ),
        ProviderSpec(
            id="openai",
            label="OpenAI",
            adapter="openai_compatible",
            api_key_env="OPENAI_API_KEY",
            headquarters="OpenAI, United States",
            max_tokens_param="max_completion_tokens",
        ),
        ProviderSpec(
            id="qwen",
            label="Alibaba Qwen",
            adapter="openai_compatible",
            api_key_env="DASHSCOPE_API_KEY",
            headquarters="Alibaba Cloud, China (international endpoint in Singapore)",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            embedding_batch_size=10,
        ),
        ProviderSpec(
            id="kimi",
            label="Moonshot Kimi",
            adapter="openai_compatible",
            api_key_env="MOONSHOT_API_KEY",
            headquarters="Moonshot AI, China",
            base_url="https://api.moonshot.ai/v1",
        ),
        ProviderSpec(
            id="deepseek",
            label="DeepSeek",
            adapter="openai_compatible",
            api_key_env="DEEPSEEK_API_KEY",
            headquarters="DeepSeek, China",
            base_url="https://api.deepseek.com",
        ),
        ProviderSpec(
            id="glm",
            label="Zhipu GLM",
            adapter="openai_compatible",
            api_key_env="ZHIPUAI_API_KEY",
            headquarters="Zhipu AI, China",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        ),
        ProviderSpec(
            id="mistral",
            label="Mistral AI",
            adapter="openai_compatible",
            api_key_env="MISTRAL_API_KEY",
            headquarters="Mistral AI, France (European Union)",
            base_url="https://api.mistral.ai/v1",
            # mistral-embed always returns 1,024 dimensions and rejects the dimensions parameter.
            embedding_dimensions_param=False,
        ),
    ]
}
