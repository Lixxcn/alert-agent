from pocketflow import AsyncFlow, Flow, AsyncNode


class LoggingAsyncFlow(AsyncFlow):
    """自定义的AsyncFlow子类，添加更多的日志记录"""
    
    async def _orch_async(self, shared, params=None):
        curr, p, last_action = self.start_node, (params or {**self.params}), None
        print(f"🚀 流程启动: 开始执行告警处理流程，起始节点: {curr.__class__.__name__}")
        
        while curr:
            curr.set_params(p)
            print(f"▶️ 当前执行: 正在处理节点 {curr.__class__.__name__}")
            
            if isinstance(curr, AsyncNode):
                last_action = await curr._run_async(shared)
            else:
                last_action = curr._run(shared)
                
            print(f"✅ 执行完成: 节点 {curr.__class__.__name__} 处理完毕，返回结果: {last_action}")
            
            curr = self.get_next_node(curr, last_action)
            if curr:
                print(f"\n\n⏭️ 下一步骤: 即将执行节点 {curr.__class__.__name__}")
            else:
                print("🏁 流程结束: 告警处理流程已全部完成！")
        
        return last_action


from nodes import (
    ReceiveAlertNode,
    AnalyzeRootCauseNode,
    ExecuteSolutionNode,
    GenerateReportNode,
)


def create_alert_handling_flow():
    """
    创建并返回一个告警处理流程。
    """
    receive_alert = ReceiveAlertNode()
    analyze_root_cause = AnalyzeRootCauseNode()
    execute_tool = ExecuteSolutionNode()  # 重命名为 execute_tool 更符合单步执行
    generate_report = GenerateReportNode()

    # 定义流程连接
    # 1. 接收告警后，进入分析阶段
    receive_alert >> analyze_root_cause

    # 2. 分析阶段的流转：
    #    - 如果决定执行工具（"execute_tool"），则进入执行工具阶段
    #    - 如果决定问题已解决（"resolved"），则进入报告阶段
    #    - 如果决定需要人工干预（"needs_manual_intervention"），则进入报告阶段
    analyze_root_cause - "execute_tool" >> execute_tool
    analyze_root_cause - "resolved" >> generate_report
    analyze_root_cause - "needs_manual_intervention" >> generate_report

    # 3. 执行工具阶段的流转：
    #    - 工具执行完成后（"tool_executed"），返回分析阶段，进行下一次决策
    execute_tool - "tool_executed" >> analyze_root_cause

    # 4. 报告生成后流程结束
    generate_report >> None

    return LoggingAsyncFlow(start=receive_alert)


if __name__ == "__main__":
    # 这是一个简单的测试用例，模拟一个告警并运行流程
    # 在实际使用中，ReceiveAlertNode 会由 HTTP 服务器触发

    # 模拟告警数据
    mock_alert = {
        "version": "4",
        "groupKey": '{}:{alertname="PodCPUUsageHigh"}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "ai-agent-webhook-receiver",
        "groupLabels": {"alertname": "PodCPUUsageHigh"},
        "commonLabels": {
            "alertname": "PodCPUUsageHigh",
            "instance": "my-app-pod-xyz",
            "namespace": "default",
            "severity": "critical",
        },
        "commonAnnotations": {
            "summary": "Pod CPU usage is high",
            "description": "CPU usage for pod my-app-pod-xyz in namespace default is above 80% for 5 minutes.",
        },
        "externalURL": "http://localhost:9093",
        "alerts": [
            {
                "labels": {
                    "alertname": "PodCPUUsageHigh",
                    "instance": "my-app-pod-xyz",
                    "namespace": "default",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Pod CPU usage is high",
                    "description": "CPU usage for pod my-app-pod-xyz in namespace default is above 80% for 5 minutes.",
                },
                "startsAt": "2025-05-26T03:00:00.000Z",
                "endsAt": "0001-01-01T00:00:00.000Z",
                "generatorURL": "http://localhost:9090/graph?g0.expr=sum%28rate%28container_cpu_usage_seconds_total%7Bnamespace%3D%22default%22%2Cpod%3D%22my-app-pod-xyz%22%7D%5B5m%5D%29%29+by+%28pod%2Cnamespace%29+%3E+0.8&g0.tab=1",
                "fingerprint": "...",
            }
        ],
    }

    print("--- Running mock alert handling flow ---")
    alert_flow = create_alert_handling_flow()

    # 模拟 shared 存储
    shared_data = {"alert_info": mock_alert}  # 直接将告警数据放入 shared["alert_info"]

    # 运行整个流程
    import asyncio
    asyncio.run(alert_flow.run_async(shared_data))

    print("\n--- Flow execution finished ---")
    print("Final shared data keys:", shared_data.keys())
    if "report" in shared_data:
        print("\nGenerated Report:\n", shared_data["report"])
    else:
        print("\nNo report generated.")
