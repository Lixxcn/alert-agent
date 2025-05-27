import asyncio
import time


async def greet(name):
    """一个简单的协程，模拟异步操作"""
    print(f"[{time.time():.2f}] Hello, {name}!")
    await asyncio.sleep(1)  # 模拟一个耗时1秒的I/O操作
    print(f"[{time.time():.2f}] Goodbye, {name}!")


async def main_async():
    """主异步函数，调用其他协程"""
    print("Starting main_async...")
    await greet("Alice")
    await greet("Bob")
    print("main_async finished.")


async def main_async2():
    """主异步函数，并发调用其他协程"""
    print(f"[{time.time():.2f}] Starting main_async...")
    # 使用 asyncio.gather() 并发运行多个协程
    await asyncio.gather(greet("Alice"), greet("Bob"))
    print(f"[{time.time():.2f}] main_async finished.")


if __name__ == "__main__":
    print(f"[{time.time():.2f}] Running asyncio.run(main_async())...")
    asyncio.run(main_async2())
    print(f"[{time.time():.2f}] Program finished.")
