"""What the live stream shows for each content block.

The point of the stream is that a human watching a run learns how it reasons
while it waits — so thinking has to be rendered, not silently dropped the way it
was before, and it has to read as reasoning rather than as the stage's answer.
"""

from claude_agent_sdk import TextBlock, ThinkingBlock, ToolUseBlock

from orchestra_engine.engine.session import _render_block


def test_thinking_is_rendered_distinctly():
    line = _render_block(ThinkingBlock(thinking="weighing two repro paths", signature="sig"), "reproduce")
    assert line is not None, "thinking must reach the stream, not be dropped"
    assert "weighing two repro paths" in line
    assert "💭" in line, "reasoning must read as reasoning, not as the stage's output"
    assert line.startswith("[reproduce]")


def test_signature_only_thinking_is_skipped():
    """Opus 4.7+ can emit thinking blocks with a signature but no text; an empty
    line teaches nobody anything, so it should produce no output at all."""
    assert _render_block(ThinkingBlock(thinking="", signature="sig"), "plan") is None
    assert _render_block(ThinkingBlock(thinking="   \n ", signature="sig"), "plan") is None


def test_text_block_is_unchanged():
    line = _render_block(TextBlock(text="  the fix is in the parser  "), "fix")
    assert line == "[fix] the fix is in the parser"


def test_empty_text_block_is_skipped():
    assert _render_block(TextBlock(text="   "), "fix") is None


def test_tool_use_carries_its_detail():
    line = _render_block(ToolUseBlock(id="t1", name="Read", input={"file_path": "src/app.py"}), "localize")
    assert line == "[localize] -> tool: Read  (src/app.py)"


def test_tool_use_without_detail_still_names_the_tool():
    line = _render_block(ToolUseBlock(id="t2", name="ListMcpResources", input={}), "localize")
    assert line == "[localize] -> tool: ListMcpResources"
