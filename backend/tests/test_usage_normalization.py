"""Per-provider usage normalization — the anti-corruption layer.

Each provider maps its own usage shape onto the canonical `Usage`. These tests pin
that mapping so a provider SDK rename can't silently zero out our token accounting.
Provider raw objects are faked with SimpleNamespace (no network).
"""
from types import SimpleNamespace

from sdk.providers import Usage
from sdk.providers.bedrock import _normalize_usage as bedrock_norm
from sdk.providers.gemini import _normalize_usage as gemini_norm
from sdk.providers.groq import _normalize_usage as groq_norm


def test_groq_maps_prompt_completion_to_input_output():
    usage = SimpleNamespace(prompt_tokens=30, completion_tokens=12, total_tokens=42)
    canonical, raw = groq_norm(usage)
    assert (canonical.input_tokens, canonical.output_tokens) == (30, 12)
    assert raw["total_tokens"] == 42


def test_groq_none_usage_is_zeroed():
    canonical, raw = groq_norm(None)
    assert canonical == Usage()
    assert raw == {}


def test_gemini_maps_token_counts_and_reasoning_and_cache():
    meta = SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=40,
        cached_content_token_count=10,
        thoughts_token_count=7,
        total_token_count=147,
    )
    canonical, raw = gemini_norm(meta)
    assert canonical.input_tokens == 100
    assert canonical.output_tokens == 40
    assert canonical.cache_read_input_tokens == 10
    assert canonical.reasoning_tokens == 7
    assert raw["total_token_count"] == 147


def test_gemini_none_meta_is_zeroed():
    canonical, raw = gemini_norm(None)
    assert canonical == Usage()
    assert raw == {}


def test_bedrock_maps_dict_keys_to_canonical():
    raw = {
        "input_tokens": 50,
        "output_tokens": 20,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 3,
    }
    canonical = bedrock_norm(raw)  # bedrock returns Usage directly (takes a dict)
    assert canonical.input_tokens == 50
    assert canonical.output_tokens == 20
    assert canonical.cache_read_input_tokens == 5
    assert canonical.cache_creation_input_tokens == 3


def test_bedrock_empty_dict_is_zeroed():
    assert bedrock_norm({}) == Usage()
    assert bedrock_norm(None) == Usage()


def test_usage_merge_prefers_nonzero():
    # stream path merges a partial message_start usage with the final message_delta usage
    a = Usage(input_tokens=10, output_tokens=0)
    b = Usage(input_tokens=0, output_tokens=25)
    merged = a.merge(b)
    assert (merged.input_tokens, merged.output_tokens) == (10, 25)
