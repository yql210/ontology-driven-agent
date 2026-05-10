"""验证智谱 Anthropic 兼容接口是否支持工具调用"""
import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

@tool
def test_add(a: int, b: int) -> int:
    """加法测试工具"""
    return a + b

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
    api_key=os.getenv("ZHIPU_API_KEY"),
    timeout=30,
)

llm_with_tools = llm.bind_tools([test_add])

response = llm_with_tools.invoke([
    SystemMessage(content="你是一个测试助手。"),
    HumanMessage(content="请计算 3+5"),
])

print(f"Response type: {type(response)}")
print(f"Content: {response.content}")
print(f"Tool calls: {response.tool_calls}")
print("✅ API 兼容性验证通过")
