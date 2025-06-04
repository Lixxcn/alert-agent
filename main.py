import asyncio
import queue  # 导入标准库的 queue 模块

from flow import create_alert_handling_flow
from utils.http_server import start_webhook_server
from utils.mcp_client import Server, ToolSession, Configuration

# 用于在 HTTP 服务器线程和主 asyncio 线程之间传递数据的队列
# 使用标准库的 queue.Queue，它是线程安全的
alert_queue = queue.Queue()


async def process_alert_from_queue():
    """
    从队列中获取告警数据并触发 PocketFlow 流程。
    """
    while True:
        try:
            # 使用 loop.run_in_executor 来在事件循环中获取同步队列的数据
            # 这样不会阻塞事件循环
            alert_data = await asyncio.get_event_loop().run_in_executor(
                None, alert_queue.get
            )

            if alert_data is None:
                print("Main: Received None from alert queue, stopping processing task.")
                alert_queue.task_done()
                break  # 接收到 None 信号，退出循环

            print(
                f"\nMain: Processing alert from queue: {alert_data.get('commonLabels', {}).get('alertname', 'N/A')}"
            )

            shared = {}
            alert_flow = create_alert_handling_flow()

            # 告警数据直接来自队列，无需手动触发 ReceiveAlertNode
            # ReceiveAlertNode 将作为 Flow 的起始节点，直接处理 shared 中的 alert_data

            # 将 alert_data 直接放入 shared 存储，供 ReceiveAlertNode 使用
            shared["alert_info"] = alert_data

            # 运行整个流程
            print("Main: 开始执行告警处理流程...")
            print("Main: 流程将依次执行: ReceiveAlertNode -> AnalyzeRootCauseNode -> [ExecuteSolutionNode | GenerateReportNode]")
            await alert_flow.run_async(shared)
            print("Main: 告警处理流程执行完成")

            print(
                f"Main: Alert processing finished for {alert_data.get('commonLabels', {}).get('alertname', 'N/A')}"
            )
            if "report" in shared:
                print("\n--- Generated Report (from main) ---\n")
                print(shared["report"])
                print("\n------------------------------------\n")
            else:
                print("Main: No report generated for this alert.")

            alert_queue.task_done()
        except asyncio.CancelledError:
            # 当任务被取消时，优雅地退出循环，避免打印 Traceback
            print("Main: Alert processing task cancelled.")
            break
        except Exception as e:
            print(f"Main: An error occurred during alert processing: {e}")
            # 确保即使发生错误，任务也能标记为完成，避免死锁
            alert_queue.task_done()


def http_alert_callback(alert_data):
    """
    HTTP 服务器接收到告警后的回调函数，将告警数据放入队列。
    """
    print(
        f"HTTP Callback: Received alert for {alert_data.get('commonLabels', {}).get('alertname', 'N/A')}. Adding to queue."
    )
    # 直接使用线程安全的 queue.Queue.put_nowait()
    alert_queue.put_nowait(alert_data)


async def main_async():
    # 启动 HTTP 服务器
    host = "0.0.0.0"
    port = 10000
    httpd, server_thread = start_webhook_server(host, port, http_alert_callback)

    # 启动一个后台任务来处理队列中的告警
    processor_task = asyncio.create_task(process_alert_from_queue())

    print(f"Alert processing system started. Listening on http://{host}:{port}/alert")
    print("Press Ctrl+C to stop the server.")

    try:
        # 保持主事件循环运行
        while True:
            await asyncio.sleep(3600)  # 保持主循环运行，每小时检查一次
    except asyncio.CancelledError:
        print("Main: Async tasks cancelled.")
    except KeyboardInterrupt:
        print("Main: KeyboardInterrupt received. Shutting down...")
    finally:
        # 清理资源
        processor_task.cancel()
        # 在取消任务后，向队列中放入一个 None，确保 run_in_executor 能够返回
        alert_queue.put_nowait(None)
        await processor_task  # 等待任务取消
        httpd.shutdown()
        httpd.server_close()
        server_thread.join()
        print("Main: Server and tasks stopped.")


def main():
    # 使用 asyncio.run() 来运行顶层协程，它会负责事件循环的创建和关闭
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
