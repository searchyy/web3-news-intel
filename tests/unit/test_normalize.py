from __future__ import annotations

from app.pipeline.entities import extract_chains, extract_symbols
from app.pipeline.normalize import canonicalize_url, clean_title


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM:443/news?a=1&utm_source=x&b=2#section")
        == "https://example.com/news?a=1&b=2"
    )


def test_clean_title_strips_source_suffix() -> None:
    assert clean_title("  Alpha launches upgrade - CoinDesk ") == "Alpha launches upgrade"


def test_symbol_and_chain_extraction() -> None:
    text = "Binance Will List Alpha Beta Coin (ABC) on Ethereum and Base"
    assert "ABC" in extract_symbols(text)
    assert extract_chains(text) == ["base", "ethereum"]
