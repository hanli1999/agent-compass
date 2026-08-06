from agent_compass.adapters import LLMAdapter, NullAdapter


def test_null_adapter_is_an_llm_adapter():
    adapter = NullAdapter()
    assert isinstance(adapter, LLMAdapter)
    assert adapter.name == "null"
    assert adapter.classify({})["refused"] is True
    assert adapter.summarize({}) == ""
