from dataclasses import asdict, dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0

    def merge(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens or other.input_tokens,
            output_tokens=self.output_tokens or other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens or other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens or other.cache_creation_input_tokens,
            reasoning_tokens=self.reasoning_tokens or other.reasoning_tokens,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProviderResult:
    text: str
    usage: Usage
    raw_usage: dict = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)


def _split_system(messages: list) -> tuple[str | None, list]:
    system = None
    convo = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            convo.append(msg)
    return system, convo
