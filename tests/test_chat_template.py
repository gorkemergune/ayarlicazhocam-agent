"""Integration tests for the standalone Gemma Jinja chat template.

The fixture writes and reloads a tiny local Hugging Face tokenizer.  This keeps
the tests offline and ungated while exercising the real
``AutoTokenizer.apply_chat_template`` implementation used by a Gemma tokenizer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

transformers = pytest.importorskip("transformers")
tokenizers = pytest.importorskip("tokenizers")

AutoTokenizer = transformers.AutoTokenizer
PreTrainedTokenizerFast = transformers.PreTrainedTokenizerFast
Tokenizer = tokenizers.Tokenizer
WordLevel = tokenizers.models.WordLevel
Whitespace = tokenizers.pre_tokenizers.Whitespace

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "chat_template.jinja"


@pytest.fixture()
def tokenizer(tmp_path: Path):
    """Load a local fast tokenizer, then attach the project chat template."""
    vocabulary = {"<unk>": 0, "<bos>": 1, "<eos>": 2, "<pad>": 3}
    backend = Tokenizer(WordLevel(vocabulary, unk_token="<unk>"))
    backend.pre_tokenizer = Whitespace()
    local_tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="<unk>",
        bos_token="<bos>",
        eos_token="<eos>",
        pad_token="<pad>",
    )
    tokenizer_dir = tmp_path / "tokenizer"
    local_tokenizer.save_pretrained(tokenizer_dir)

    loaded = AutoTokenizer.from_pretrained(tokenizer_dir)
    loaded.chat_template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return loaded


def _render(tokenizer, messages: list[dict], **kwargs: object) -> str:
    return tokenizer.apply_chat_template(messages, tokenize=False, **kwargs)


def _tagged_json(prompt: str, tag: str) -> list[dict]:
    values = re.findall(rf"<{tag}>(.*?)</{tag}>", prompt)
    return [json.loads(value) for value in values]


def test_system_and_user_are_rendered_in_a_gemma_user_turn(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {"role": "system", "content": "Use tools for stored task data."},
            {"role": "user", "content": "List my tasks."},
        ],
        add_generation_prompt=True,
    )

    assert prompt == (
        "<bos><start_of_turn>user\n"
        "<system>Use tools for stored task data.</system>\n"
        "List my tasks.<end_of_turn>\n<start_of_turn>model\n"
    )


def test_user_and_assistant_turns_render_without_a_generation_prompt(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {"role": "user", "content": "Merhaba"},
            {"role": "assistant", "content": "Merhaba, nasıl yardımcı olayım?"},
        ],
    )

    assert "<start_of_turn>user\nMerhaba<end_of_turn>\n" in prompt
    assert "<start_of_turn>model\nMerhaba, nasıl yardımcı olayım?<end_of_turn>\n" in prompt
    assert not prompt.endswith("<start_of_turn>model\n")


def test_multiturn_conversation_preserves_each_turn(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {"role": "user", "content": "Bugün ne yapmalıyım?"},
            {"role": "assistant", "content": "Önceliklerini kontrol edeyim."},
            {"role": "user", "content": "Yüksek önceliklileri göster."},
        ],
        add_generation_prompt=True,
    )

    assert prompt.count("<start_of_turn>user\n") == 2
    assert prompt.count("<start_of_turn>model\n") == 2
    assert "Yüksek önceliklileri göster." in prompt


def test_assistant_tool_call_is_distinguishable_and_json_serialized(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {"role": "user", "content": "Gecikmiş görevler var mı?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_tasks", "arguments": {"overdue": True}},
                    }
                ],
            },
        ],
    )

    assert _tagged_json(prompt, "tool_call") == [
        {"name": "get_tasks", "arguments": {"overdue": True}}
    ]
    assert "<start_of_turn>model\n<tool_call>" in prompt


def test_tool_response_is_grouped_in_a_distinguishable_user_turn(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {
                "role": "tool",
                "name": "get_tasks",
                "tool_call_id": "call_1",
                "content": '{"tasks": [], "count": 0}',
            }
        ],
    )

    assert "<start_of_turn>user\n<tool_response>" in prompt
    assert _tagged_json(prompt, "tool_response") == [
        {
            "name": "get_tasks",
            "content": '{"tasks": [], "count": 0}',
            "tool_call_id": "call_1",
        }
    ]


def test_multiple_tool_calls_and_responses_are_serialized(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_tasks", "arguments": {"priority": "high"}}},
                    {"function": {"name": "get_tasks", "arguments": {"overdue": True}}},
                ],
            },
            {"role": "tool", "name": "get_tasks", "content": '{"count": 2}'},
            {"role": "tool", "name": "get_tasks", "content": '{"count": 1}'},
        ],
    )

    assert _tagged_json(prompt, "tool_call") == [
        {"name": "get_tasks", "arguments": {"priority": "high"}},
        {"name": "get_tasks", "arguments": {"overdue": True}},
    ]
    assert len(_tagged_json(prompt, "tool_response")) == 2
    assert prompt.count("<start_of_turn>user\n") == 1


def test_final_assistant_response_follows_a_tool_response(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [
            {"role": "user", "content": "Görevlerimi listele."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "get_tasks", "arguments": {}}}],
            },
            {"role": "tool", "name": "get_tasks", "content": '{"count": 1}'},
            {"role": "assistant", "content": "Bir görevin var."},
            {"role": "user", "content": "Teşekkürler."},
        ],
        add_generation_prompt=True,
    )

    assert "<tool_response>" in prompt
    assert "<start_of_turn>model\nBir görevin var.<end_of_turn>\n" in prompt
    assert prompt.endswith("<start_of_turn>model\n")


def test_empty_history_and_tokenization_are_supported(tokenizer) -> None:
    # Transformers rejects ``[]`` before it renders a template.  A one-item
    # batch containing an empty history exercises the same template branch.
    prompts = tokenizer.apply_chat_template(
        [[]], tokenize=False, add_generation_prompt=True
    )
    token_ids = tokenizer.apply_chat_template(
        [[]], tokenize=True, add_generation_prompt=True
    )

    assert prompts == ["<bos><start_of_turn>model\n"]
    assert token_ids["input_ids"]


def test_rendering_is_whitespace_stable(tokenizer) -> None:
    messages = [{"role": "user", "content": "  Aynı istem  "}]

    first = _render(tokenizer, messages, add_generation_prompt=True)
    second = _render(tokenizer, messages, add_generation_prompt=True)

    assert first == second
    assert "\n\n" not in first
    assert "Aynı istem" in first


def test_optional_tool_definitions_are_serialized(tokenizer) -> None:
    prompt = _render(
        tokenizer,
        [{"role": "user", "content": "Görevleri getir."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_tasks",
                    "description": "List stored tasks.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert "<tools>" in prompt
    assert '"get_tasks"' in prompt
