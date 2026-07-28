"""Reversible PII tokenization — round-trip and safety invariants.

The contract the chatbot relies on: the LLM only ever sees `[PII:TYPE:N]` tokens,
the user always sees the original text, and detokenize NEVER raises (a missing key
just leaves the token in place).
"""

import pytest

from inferscope.pii_tokenizer import _TOKEN_RE, PiiTokenizer

tk = PiiTokenizer()


@pytest.mark.parametrize(
    "text",
    [
        "email me at john.doe@example.com please",
        "call +919876543210 or my US line (415) 555-0132",
        "SSN 123-45-6789 and PAN ABCDE1234F",
        "mix: a@b.com, 987-65-4320, ZYXWV9876K all at once",
    ],
)
def test_round_trip_restores_original(text):
    tokenized, pii_map = tk.tokenize(text)
    assert pii_map is not None, "expected PII to be detected"
    assert tk.detokenize(tokenized, pii_map) == text


def test_tokenized_text_hides_the_raw_value():
    text = "reach me at secret@hidden.com"
    tokenized, _ = tk.tokenize(text)
    assert "secret@hidden.com" not in tokenized
    assert "[PII:EMAIL:1]" in tokenized


def test_no_pii_returns_none_map_and_unchanged_text():
    text = "just a normal sentence with no sensitive data"
    tokenized, pii_map = tk.tokenize(text)
    assert tokenized == text
    assert pii_map is None
    # detokenize with a None map is a no-op and must not raise
    assert tk.detokenize(text, None) == text


def test_emitted_token_format_is_square_bracket_and_one_based():
    # Two emails -> EMAIL:1, EMAIL:2 (1-based, per type).
    _, pii_map = tk.tokenize("a@x.com then b@y.com")
    assert set(pii_map.keys()) == {"EMAIL:1", "EMAIL:2"}
    assert pii_map["EMAIL:1"] == "a@x.com"
    assert pii_map["EMAIL:2"] == "b@y.com"


def test_missing_key_is_left_in_place_and_never_raises():
    # Token present in text but absent from the map -> returned verbatim, no crash.
    out = tk.detokenize("hello [PII:EMAIL:9]", {"EMAIL:1": "a@b.com"})
    assert out == "hello [PII:EMAIL:9]"


def test_legacy_angle_bracket_tokens_are_not_matched():
    # Old format <pii:EMAIL:1> must NOT be touched by the new square-bracket regex.
    legacy = "old token <pii:EMAIL:1> stays"
    assert _TOKEN_RE.search(legacy) is None
    assert tk.detokenize(legacy, {"EMAIL:1": "a@b.com"}) == legacy


def test_empty_input():
    assert tk.tokenize("") == ("", None)
    assert tk.detokenize("", {"EMAIL:1": "x"}) == ""


def test_detokenize_only_replaces_known_tokens_in_mixed_text():
    text = "user a@b.com sent 123-45-6789"
    tokenized, pii_map = tk.tokenize(text)
    # every emitted token must map back; nothing dangling
    for m in _TOKEN_RE.finditer(tokenized):
        key = f"{m.group(1)}:{m.group(2)}"
        assert key in pii_map
    assert tk.detokenize(tokenized, pii_map) == text
