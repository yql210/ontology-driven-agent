"""Agent 评估评分脚本

运行评估集并生成评分报告。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from layerkg.agent.graph import create_agent


def load_eval_set(eval_set_path: Path) -> dict[str, Any]:
    """加载评估集 JSON"""
    with open(eval_set_path) as f:
        return json.load(f)


def extract_tool_calls(messages: list[dict]) -> list[str]:
    """从 Agent 执行历史中提取工具调用名称"""
    tools_called = []
    for msg in messages:
        if msg.get("type") == "ai":
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                name = tc.get("name", "")
                if name:
                    tools_called.append(name)
        elif msg.get("type") == "tool":
            # Tool messages also record which tool was called
            name = msg.get("name", "")
            if name and name not in tools_called:
                tools_called.append(name)
    return tools_called


def calculate_tool_match(expected: list[str], actual: list[str]) -> float:
    """计算工具调用覆盖率（0.0-1.0）"""
    if not expected:
        return 1.0
    expected_set = set(expected)
    actual_set = set(actual)
    covered = expected_set & actual_set
    return round(len(covered) / len(expected_set), 2)


def calculate_answer_match(expected: dict, actual: str) -> float:
    """根据答案类型计算匹配分数"""
    answer_type = expected.get("type", "exact")
    actual_lower = actual.lower().strip()

    if answer_type == "exact":
        expected_value = str(expected.get("value", "")).lower().strip()
        if expected_value not in actual_lower:
            return 0.0
        words = re.findall(r"[a-z_]+", actual_lower)
        for w in words:
            if expected_value in w and w != expected_value:
                return 0.0
        return 1.0

    elif answer_type == "contains":
        expected_value = str(expected.get("value", "")).lower()
        return 1.0 if expected_value in actual_lower else 0.0

    elif answer_type == "list":
        expected_list = [str(v).lower() for v in expected.get("value", [])]
        matched_count = sum(1 for v in expected_list if v in actual_lower)
        return matched_count / len(expected_list) if expected_list else 0.0

    elif answer_type == "fuzzy":
        keywords = [str(k).lower() for k in expected.get("keywords", [])]
        matched_count = sum(1 for k in keywords if k in actual_lower)
        return matched_count / len(keywords) if keywords else 0.0

    return 0.0


async def run_single_question(
    question: dict[str, Any],
    agent: Any,
    thread_id: str,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """运行单个评估问题"""
    q_id = question["id"]
    q_level = question["level"]
    q_text = question["question"]
    expected_tools = question["expected_tools"]
    expected_answer = question["expected_answer"]

    start_time = time.time()

    try:
        # 调用 Agent (异步)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=q_text)]},
                config=config,
            ),
            timeout=timeout_sec,
        )

        # 提取实际工具调用
        actual_tools = []
        messages = []
        for msg in result.get("messages", []):
            # Convert messages to dict for serialization
            if hasattr(msg, "model_dump"):
                messages.append(msg.model_dump())
            elif hasattr(msg, "dict"):
                messages.append(msg.dict())
            else:
                messages.append(msg)

        # Extract tool calls from AIMessage
        for msg in messages:
            msg_type = msg.get("type", "")
            if msg_type == "ai":
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    name = tc.get("name", "")
                    if name:
                        actual_tools.append(name)
            elif msg_type == "tool":
                name = msg.get("name", "")
                if name and name not in actual_tools:
                    actual_tools.append(name)

        # 去重
        actual_tools = list(dict.fromkeys(actual_tools))

        # 提取最终回答
        actual_answer = ""
        for msg in reversed(messages):
            if msg.get("type") == "ai" and msg.get("content"):
                content = msg.get("content")
                if isinstance(content, str):
                    actual_answer = content
                    break
                elif isinstance(content, list):
                    # Handle structured content
                    actual_answer = json.dumps(content, ensure_ascii=False)
                    break

        duration = time.time() - start_time

        # 计算工具匹配
        tool_match = calculate_tool_match(expected_tools, actual_tools)

        # 计算答案匹配
        answer_score = calculate_answer_match(expected_answer, actual_answer)

        # 根据等级计算总分
        if q_level == 1:
            total_score = tool_match * 0.3 + answer_score * 0.7
        elif q_level == 2:
            total_score = tool_match * 0.4 + answer_score * 0.6
        else:  # level 3
            total_score = tool_match * 0.5 + answer_score * 0.5

        return {
            "id": q_id,
            "question": q_text,
            "level": q_level,
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "expected_answer": expected_answer,
            "actual_answer": actual_answer[:2000] if len(actual_answer) > 2000 else actual_answer,
            "tool_match": tool_match,
            "answer_match": answer_score,
            "score": total_score,
            "duration_sec": round(duration, 2),
            "error": None,
        }

    except TimeoutError:
        duration = time.time() - start_time
        return {
            "id": q_id,
            "question": q_text,
            "level": q_level,
            "expected_tools": expected_tools,
            "actual_tools": [],
            "expected_answer": expected_answer,
            "actual_answer": "",
            "tool_match": 0.0,
            "answer_match": 0.0,
            "score": 0.0,
            "duration_sec": round(duration, 2),
            "error": "timeout",
        }
    except Exception as e:
        duration = time.time() - start_time
        return {
            "id": q_id,
            "question": q_text,
            "level": q_level,
            "expected_tools": expected_tools,
            "actual_tools": [],
            "expected_answer": expected_answer,
            "actual_answer": "",
            "tool_match": 0.0,
            "answer_match": 0.0,
            "score": 0.0,
            "duration_sec": round(duration, 2),
            "error": str(e),
        }


async def run_evaluation(
    eval_set_path: Path,
    output_path: Path,
    level_filter: str = "all",
    limit: int | None = None,
) -> dict[str, Any]:
    """运行完整评估"""
    # 加载评估集
    eval_data = load_eval_set(eval_set_path)
    questions = eval_data["questions"]

    # 过滤等级
    if level_filter != "all":
        questions = [q for q in questions if q["level"] == int(level_filter.replace("L", ""))]

    # 限制数量
    if limit:
        questions = questions[:limit]

    # 创建 Agent
    agent = create_agent()

    # 运行评估（串行，避免并发问题）
    details = []
    for i, question in enumerate(questions):
        print(f"\n[{i + 1}/{len(questions)}] Running {question['id']}: {question['question'][:50]}...")
        thread_id = f"eval_{question['id']}_{int(time.time())}"

        result = await run_single_question(question, agent, thread_id)
        details.append(result)

        print(f"  Score: {result['score']:.2f} | Tools: {result['actual_tools']}")
        await asyncio.sleep(2)  # 题间延迟，避免 API 限流

    # 计算汇总
    total = len(details)
    correct = sum(1 for d in details if d["score"] >= 0.7)
    avg_score = sum(d["score"] for d in details) / total if total > 0 else 0.0

    by_level = {}
    for level in [1, 2, 3]:
        level_details = [d for d in details if d["level"] == level]
        if level_details:
            level_total = len(level_details)
            level_correct = sum(1 for d in level_details if d["score"] >= 0.7)
            level_avg = sum(d["score"] for d in level_details) / level_total
            by_level[f"L{level}"] = {
                "total": level_total,
                "correct": level_correct,
                "avg_score": round(level_avg, 3),
            }

    report = {
        "timestamp": datetime.now().isoformat(),
        "eval_set_version": eval_data.get("version"),
        "summary": {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 3) if total > 0 else 0.0,
            "avg_score": round(avg_score, 3),
            "by_level": by_level,
        },
        "details": details,
    }

    # 保存报告
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n\n=== Evaluation Report ===")
    print(f"Total: {total}")
    print(f"Correct (score>=0.7): {correct}")
    print(f"Accuracy: {report['summary']['accuracy']:.2%}")
    print(f"Avg Score: {avg_score:.3f}")
    print("\nBy Level:")
    for level, stats in by_level.items():
        print(f"  {level}: {stats['correct']}/{stats['total']} correct, avg={stats['avg_score']:.3f}")
    print(f"\nReport saved to: {output_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent evaluation")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=Path(__file__).parent / "eval_set.json",
        help="Path to eval_set.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "eval_report.json",
        help="Path to save eval_report.json",
    )
    parser.add_argument(
        "--questions",
        choices=["L1", "L2", "L3", "all"],
        default="all",
        help="Filter questions by level",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of questions to run",
    )

    args = parser.parse_args()

    asyncio.run(
        run_evaluation(
            eval_set_path=args.eval_set,
            output_path=args.output,
            level_filter=args.questions,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
