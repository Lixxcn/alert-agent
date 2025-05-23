from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()


# Learn more about calling the LLM: https://the-pocket.github.io/PocketFlow/utility_function/llm.html
def call_llm(prompt):
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "your-api-key"))
    r = client.chat.completions.create(
        model="deepseek-chat", messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content


if __name__ == "__main__":
    prompt = "什么是LLM?"
    print(call_llm(prompt))
