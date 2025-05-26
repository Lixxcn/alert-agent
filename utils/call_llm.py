from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()


# Learn more about calling the LLM: https://the-pocket.github.io/PocketFlow/utility_function/llm.html
def call_llm(messages, model="deepseek-chat"):
    """
    调用 LLM 模型。
    :param messages: 消息列表，例如 [{"role": "user", "content": "Hello"}]
    :param model: 要使用的模型名称
    :return: LLM 的响应内容
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "your-api-key"))
    r = client.chat.completions.create(model=model, messages=messages)
    return r.choices[0].message.content


if __name__ == "__main__":
    # 示例用法
    print("--- Test: Simple prompt ---")
    messages = [{"role": "user", "content": "什么是LLM?"}]
    print(call_llm(messages))

    print("\n--- Test: Structured output prompt ---")
    structured_prompt = """
请将以下文本总结为 YAML 格式，包含一个 'summary' 字段，其值为一个包含 3 个要点的列表。

文本：
人工智能（AI）正在迅速发展，并在各个领域产生深远影响。它涵盖了机器学习、深度学习、自然语言处理等多个子领域。AI 的应用包括自动驾驶、医疗诊断、智能客服等。

输出格式：
```yaml
summary:
  - 要点1
  - 要点2
  - 要点3
```
"""
    messages_structured = [{"role": "user", "content": structured_prompt}]
    response_structured = call_llm(messages_structured)
    print(response_structured)

    try:
        import yaml

        # 尝试从响应中提取 YAML
        yaml_str = response_structured.split("```yaml")[1].split("```")[0].strip()
        parsed_yaml = yaml.safe_load(yaml_str)
        print("\nParsed YAML:")
        print(parsed_yaml)
    except Exception as e:
        print(f"\nCould not parse YAML: {e}")
