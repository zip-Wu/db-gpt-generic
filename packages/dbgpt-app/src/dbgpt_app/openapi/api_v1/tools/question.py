"""question tool — ask user questions and block until answered."""

import asyncio
import json
import logging
from typing import Any, Callable, Dict

from dbgpt.agent.resource.tool.base import tool

from .question_manager import question_manager

logger = logging.getLogger(__name__)

_DESCRIPTION = """\
Use this tool when you need to ask the user questions during \
execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- IMPORTANT: Always write the question text, header, option \
labels, and descriptions in the SAME language the user is using. \
If the user writes in English, all content must be in English; \
if in Chinese, use Chinese, etc.
- When `custom` is enabled (default), a "Type your own answer" \
option is added automatically; don't include "Other" or \
catch-all options
- Answers are returned as arrays of labels; set `multiple: true` \
to allow selecting more than one
- If you recommend a specific option, make that the first option \
in the list and add "(Recommended)" at the end of the label
Parameter: {"questions": [{"question": "...", "header": "...", \
"options": [{"label": "...", "description": "..."}], \
"multiple": false}]}
"""


def make_question(react_state: Dict[str, Any], stream_callback: Callable):
    """Return a ``question`` FunctionTool bound to react_state and stream_callback."""

    @tool(description=_DESCRIPTION)
    async def question(questions: str) -> str:
        """Ask the user one or more questions and wait for their answers.

        Args:
            questions: JSON string of a list of question objects. Each question has:
                - question (str): Complete question text
                - header (str): Very short label (max 30 chars)
                - options (list): [{label, description}] available choices
                - multiple (bool, optional): allow multi-select
        """
        conv_id = react_state.get("conv_id", "default")

        # Parse questions JSON
        try:
            parsed_questions = (
                json.loads(questions) if isinstance(questions, str) else questions
            )
            if not isinstance(parsed_questions, list):
                parsed_questions = parsed_questions.get("questions", [])
        except Exception as e:
            # 【现场适配·JSON 容错】模型生成的问题文案常混入英文双引号
            # （如 名为"施飞龙"），破坏 JSON 字符串结构导致解析失败。
            # 兜底修复：把字符串内容里的英文裸引号替换为中文引号后重试。
            repaired = None
            try:
                if isinstance(questions, str):
                    import re as _r

                    _repaired_str = _r.sub(
                        r'(?<=[\u4e00-\u9fffA-Za-z0-9])"(?=[\u4e00-\u9fffA-Za-z0-9])',
                        "「」",
                        questions,
                    )
                    # 仍失败的极端情况：直接尝试把未配对英文引号全换中文引号
                    if _repaired_str.count('"') % 2 != 0:
                        _repaired_str = _repaired_str.replace('"', "「」")
                    _parsed = json.loads(_repaired_str)
                    parsed_questions = (
                        _parsed
                        if isinstance(_parsed, list)
                        else _parsed.get("questions", [])
                    )
                    if parsed_questions:
                        repaired = parsed_questions
            except Exception:
                pass
            if repaired is None:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error: invalid questions JSON — {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        # 1. Register in QuestionManager → creates asyncio.Event
        pq = question_manager.create(conv_id=conv_id, questions=parsed_questions)

        # 2. Push question.asked SSE event to frontend
        await stream_callback(
            "question.asked",
            {
                "request_id": pq.request_id,
                "conv_id": conv_id,
                "questions": parsed_questions,
            },
        )
        logger.info(
            "question tool: pushed question.asked, request_id=%s", pq.request_id
        )

        # 3. Block until user answers (or timeout)
        try:
            await asyncio.wait_for(pq.event.wait(), timeout=300)
        except asyncio.TimeoutError:
            question_manager.remove(pq.request_id)
            await stream_callback(
                "question.rejected", {"request_id": pq.request_id, "conv_id": conv_id}
            )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": (
                                "Question timed out after 300 seconds."
                                " Proceeding without user answer."
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            )
        finally:
            question_manager.remove(pq.request_id)

        # 4. User rejected
        if pq.rejected:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": (
                                "The user dismissed the question."
                                " Proceeding without answer."
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            )

        # 5. Format answers for the LLM
        answers = pq.answers or []
        parts = []
        for i, q in enumerate(parsed_questions):
            q_text = q.get("question", "")
            a_text = (
                ", ".join(answers[i])
                if i < len(answers) and answers[i]
                else "Unanswered"
            )
            parts.append(f'"{q_text}"="{a_text}"')
        formatted = ", ".join(parts)
        output = (
            f"User has answered your questions: {formatted}."
            " You can now continue with the user's answers"
            " in mind."
        )
        return json.dumps(
            {"chunks": [{"output_type": "text", "content": output}]},
            ensure_ascii=False,
        )

    return question
