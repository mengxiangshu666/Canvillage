from types import SimpleNamespace

from novelvideo.cognee import config


def test_custom_relay_embedding_uses_tiktoken_without_huggingface_lookup(monkeypatch):
    calls: list[tuple[str | None, int]] = []

    class FakeTokenizer:
        def __init__(self, *, model, max_completion_tokens):
            calls.append((model, max_completion_tokens))

    class FakeEngine:
        provider = "custom"
        endpoint = "http://relay.invalid/v1"
        max_completion_tokens = 321

        def get_tokenizer(self):
            return "stock"

    original_import = config.importlib.import_module

    def fake_import(name: str):
        if name == "cognee.infrastructure.llm.tokenizer.TikToken":
            return SimpleNamespace(TikTokenTokenizer=FakeTokenizer)
        return original_import(name)

    monkeypatch.setattr(config.importlib, "import_module", fake_import)
    config._patch_cognee_embedding_tokenizer(FakeEngine)

    tokenizer = FakeEngine().get_tokenizer()

    assert isinstance(tokenizer, FakeTokenizer)
    assert calls == [(None, 321)]


def test_non_relay_embedding_keeps_stock_tokenizer():
    class FakeEngine:
        provider = "huggingface"
        endpoint = ""
        max_completion_tokens = 10

        def get_tokenizer(self):
            return "stock"

    config._patch_cognee_embedding_tokenizer(FakeEngine)

    assert FakeEngine().get_tokenizer() == "stock"
