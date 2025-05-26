from pocketflow import Node, AsyncNode
from utils.call_llm import call_llm
from utils.k8s_tools import execute_k8s_command
import json
import yaml  # 用于解析 LLM 返回的结构化 YAML
import time


class ReceiveAlertNode(Node):
    """
    接收 Alertmanager 告警的节点。
    这个节点从 shared 存储中获取告警数据。
    """

    def prep(self, shared):
        alert_info = shared.get("alert_info")
        if not alert_info:
            raise ValueError("ReceiveAlertNode: Missing 'alert_info' in shared store.")
        print(f"ReceiveAlertNode: Preparing to process alert data from shared.")
        return alert_info

    def exec(self, alert_info):
        # exec 方法接收 prep 方法返回的 alert_info
        print(f"ReceiveAlertNode: Processing alert data.")
        return alert_info

    def post(self, shared, prep_res, exec_res):
        # exec_res 已经是处理后的 alert_info
        # 确保 alert_info 已经存在于 shared 中
        print(
            f"ReceiveAlertNode: Alert data processed. Alert name: {exec_res.get('commonLabels', {}).get('alertname', 'N/A')}"
        )
        return "default"


class AnalyzeRootCauseNode(Node):
    """
    分析告警根因，并决定下一步要执行的 K8s 工具或宣布问题解决。
    """

    def prep(self, shared):
        alert_info = shared.get("alert_info")
        # 历史执行记录，用于 LLM 决策
        history = shared.get("execution_history", [])
        if not alert_info:
            raise ValueError(
                "AnalyzeRootCauseNode: Missing 'alert_info' in shared store."
            )
        return alert_info, history

    def exec(self, inputs):
        alert_info, history = inputs
        alert_name = alert_info.get("commonLabels", {}).get("alertname", "未知告警")
        severity = alert_info.get("commonLabels", {}).get("severity", "未知")
        instance = alert_info.get("commonLabels", {}).get("instance", "未知实例")

        # 模拟可用的 K8s 工具列表
        available_k8s_tools = [
            {
                "name": "get_pod_logs",
                "description": "获取指定 Pod 的日志",
                "parameters": {"pod_name": "str", "namespace": "str"},
            },
            {
                "name": "describe_pod",
                "description": "获取指定 Pod 的详细描述信息",
                "parameters": {"pod_name": "str", "namespace": "str"},
            },
            {
                "name": "restart_deployment",
                "description": "重启指定 Deployment",
                "parameters": {"deployment_name": "str", "namespace": "str"},
            },
            {
                "name": "scale_deployment",
                "description": "扩缩容指定 Deployment 的副本数",
                "parameters": {
                    "deployment_name": "str",
                    "replicas": "int",
                    "namespace": "str",
                },
            },
            # 更多 K8s 工具...
        ]

        tools_str = json.dumps(available_k8s_tools, indent=2, ensure_ascii=False)
        history_str = json.dumps(history, indent=2, ensure_ascii=False)

        prompt = f"""
你是一个专业的 Kubernetes 告警分析和决策助手。
当前接收到一个告警，你的任务是根据告警信息和历史执行结果，决定下一步要执行的 K8s 工具，或者判断问题是否已解决。

告警信息：
{json.dumps(alert_info, indent=2, ensure_ascii=False)}

历史执行记录（如果存在）：
{history_str if history else "无历史执行记录。"}

可用的 K8s 工具：
{tools_str}

请以 YAML 格式输出你的决策。
输出必须包含 'decision' 字段，其值可以是 'execute_tool' 或 'resolved' 或 'needs_manual_intervention'。

如果 'decision' 是 'execute_tool'，则必须包含 'tool_call' 字段，其值是一个字典，包含 'tool_name' 和 'parameters' 字段。
如果 'decision' 是 'resolved' 或 'needs_manual_intervention'，则必须包含 'reason' 字段，说明原因。

示例输出格式：
```yaml
decision: execute_tool
tool_call:
  tool_name: get_pod_logs
  parameters:
    pod_name: my-app-pod-xyz
    namespace: default
```

或者：
```yaml
decision: resolved
reason: Pod CPU usage has returned to normal after scaling down.
```

或者：
```yaml
decision: needs_manual_intervention
reason: Multiple attempts to restart deployment failed, requiring human diagnosis.
```
"""
        messages = [{"role": "user", "content": prompt}]

        try:
            llm_response = call_llm(messages)
            print(f"AnalyzeRootCauseNode: LLM raw response:\n{llm_response}")

            # 尝试从 LLM 响应中提取 YAML
            yaml_str = llm_response.split("```yaml")[1].split("```")[0].strip()
            parsed_result = yaml.safe_load(yaml_str)

            # 验证结构
            if not isinstance(parsed_result, dict) or "decision" not in parsed_result:
                raise ValueError("LLM response missing required 'decision' field.")

            decision = parsed_result["decision"]
            if decision == "execute_tool":
                if "tool_call" not in parsed_result:
                    raise ValueError(
                        "'tool_call' field missing for 'execute_tool' decision."
                    )
                if (
                    not isinstance(parsed_result["tool_call"], dict)
                    or "tool_name" not in parsed_result["tool_call"]
                    or "parameters" not in parsed_result["tool_call"]
                ):
                    raise ValueError("Invalid 'tool_call' format.")
            elif decision in ["resolved", "needs_manual_intervention"]:
                if "reason" not in parsed_result:
                    raise ValueError(
                        "'reason' field missing for 'resolved' or 'needs_manual_intervention' decision."
                    )
            else:
                raise ValueError(f"Invalid decision: {decision}")

            return parsed_result
        except Exception as e:
            print(
                f"AnalyzeRootCauseNode: Error parsing LLM response or invalid decision: {e}"
            )
            # 如果解析失败，返回一个需要人工干预的决策
            return {
                "decision": "needs_manual_intervention",
                "reason": f"LLM decision parsing failed: {e}",
            }

    def post(self, shared, prep_res, exec_res):
        # exec_res 包含 LLM 的决策结果
        decision = exec_res["decision"]

        if decision == "execute_tool":
            shared["current_tool_call"] = exec_res["tool_call"]
            print(
                f"AnalyzeRootCauseNode: Decided to execute tool: {exec_res['tool_call']['tool_name']}"
            )
            return "execute_tool"
        elif decision == "resolved":
            shared["resolution_reason"] = exec_res["reason"]
            print(
                f"AnalyzeRootCauseNode: Problem resolved. Reason: {exec_res['reason']}"
            )
            return "resolved"
        elif decision == "needs_manual_intervention":
            shared["manual_intervention_reason"] = exec_res["reason"]
            print(
                f"AnalyzeRootCauseNode: Needs manual intervention. Reason: {exec_res['reason']}"
            )
            return "needs_manual_intervention"
        else:
            # 兜底，如果 LLM 返回了未知决策
            print(
                f"AnalyzeRootCauseNode: Unknown decision from LLM: {decision}. Defaulting to manual intervention."
            )
            shared["manual_intervention_reason"] = f"Unknown LLM decision: {decision}"
            return "needs_manual_intervention"


class ExecuteSolutionNode(Node):
    """
    执行单个 K8s 工具调用的节点。
    """

    def prep(self, shared):
        tool_call = shared.get("current_tool_call")
        if not tool_call:
            raise ValueError(
                "ExecuteSolutionNode: Missing 'current_tool_call' in shared store."
            )
        return tool_call

    def exec(self, tool_call):
        tool_name = tool_call.get("tool_name")
        parameters = tool_call.get("parameters", {})

        print(
            f"ExecuteSolutionNode: Executing tool {tool_name} with parameters {parameters}"
        )
        try:
            result = execute_k8s_command(tool_name, parameters)
            return {
                "tool_call": tool_call,
                "status": result.get("status", "unknown"),
                "output": result.get(
                    "output", result.get("message", "No output/message.")
                ),
            }
        except Exception as e:
            return {
                "tool_call": tool_call,
                "status": "error",
                "output": f"Exception during tool execution: {e}",
            }

    def post(self, shared, prep_res, exec_res):
        # 将本次执行结果添加到历史记录中
        if "execution_history" not in shared:
            shared["execution_history"] = []
        shared["execution_history"].append(exec_res)

        print(
            f"ExecuteSolutionNode: Tool {exec_res['tool_call']['tool_name']} execution status: {exec_res['status']}"
        )

        # 无论成功或失败，都返回到 AnalyzeRootCauseNode 进行下一次决策
        return "tool_executed"


class GenerateReportNode(Node):
    """
    生成告警处理报告的节点。
    """

    def prep(self, shared):
        alert_info = shared.get("alert_info", {})
        analysis_result = shared.get("analysis_result", "No analysis available.")
        tool_chain = shared.get("tool_chain", [])
        execution_results = shared.get("execution_results", [])
        all_execution_successful = shared.get("all_execution_successful", False)

        return {
            "alert_info": alert_info,
            "analysis_result": analysis_result,
            "tool_chain": tool_chain,
            "execution_results": execution_results,
            "all_execution_successful": all_execution_successful,
        }

    def exec(self, inputs):
        alert_info = inputs["alert_info"]
        analysis_result = inputs["analysis_result"]
        tool_chain = inputs["tool_chain"]
        execution_results = inputs["execution_results"]
        all_execution_successful = inputs["all_execution_successful"]

        alert_name = alert_info.get("commonLabels", {}).get("alertname", "N/A")
        severity = alert_info.get("commonLabels", {}).get("severity", "N/A")
        instance = alert_info.get("commonLabels", {}).get("instance", "N/A")
        starts_at = alert_info.get("startsAt", "N/A")

        status_text = "已成功处理" if all_execution_successful else "处理失败或部分失败"

        report_prompt = f"""
请根据以下信息生成一份详细的告警处理报告，格式为 Markdown。

---
# 告警处理报告

## 告警概述
- **告警名称**: {alert_name}
- **严重性**: {severity}
- **实例**: {instance}
- **告警时间**: {starts_at}
- **当前处理状态**: {status_text}

## 根因分析与解决方案
{analysis_result}

## 执行步骤与结果
"""
        if tool_chain:
            report_prompt += "### 计划执行的工具链：\n"
            for i, tool in enumerate(tool_chain):
                report_prompt += f"- **步骤 {i+1}**: 工具 `{tool.get('tool_name')}`，参数 `{json.dumps(tool.get('parameters', {}), ensure_ascii=False)}`\n"

            report_prompt += "\n### 实际执行结果：\n"
            for result in execution_results:
                report_prompt += (
                    f"- **步骤 {result['step']}**: 工具 `{result['tool_name']}`\n"
                )
                report_prompt += f"  - **状态**: {result['status']}\n"
                report_prompt += f"  - **输出/错误**: \n```\n{result['output']}\n```\n"
        else:
            report_prompt += "未生成工具调用链或工具链为空。\n"

        report_prompt += """
---
"""
        messages = [{"role": "user", "content": report_prompt}]
        llm_response = call_llm(messages)
        return llm_response

    def post(self, shared, prep_res, exec_res):
        shared["report"] = exec_res
        print(
            "GenerateReportNode: Alert processing report generated and stored in shared."
        )
        print("\n--- FINAL REPORT ---\n")
        print(exec_res)
        print("\n--------------------\n")
        return "default"
