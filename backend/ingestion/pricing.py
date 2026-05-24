from decimal import Decimal


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    rates: tuple[float, float] | None,
) -> float | None:
    if rates is None:
        return None
    in_rate, out_rate = rates
    cost = Decimal(input_tokens) * Decimal(str(in_rate)) + Decimal(output_tokens) * Decimal(str(out_rate))
    return float(cost)
