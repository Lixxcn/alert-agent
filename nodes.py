from pocketflow import Node, AsyncNode
from utils import mcp_client
from utils.call_llm import call_llm
from utils.mcp_client import MCPClient
import json
import yaml  # 用于解析 LLM 返回的结构化 YAML


class ReceiveAlertNode(AsyncNode):
    """
    接收 Alertmanager 告警的节点。
    这个节点从 shared 存储中获取告警数据。
    """

    async def prep_async(self, shared):
        alert_info = shared.get("alert_info")
        if not alert_info:
            raise ValueError("ReceiveAlertNode: 共享存储中缺少'alert_info'数据")
        print(f"📨 接收告警: 准备处理共享存储中的告警数据")
        return alert_info

    async def exec_async(self, alert_info):
        # exec 方法接收 prep 方法返回的 alert_info
        print(f"🔍 处理告警: 正在解析告警数据内容")
        return alert_info

    async def post_async(self, shared, prep_res, exec_res):
        # exec_res 已经是处理后的 alert_info
        # 确保 alert_info 已经存在于 shared 中
        print(
            f"📋 告警就绪: 已完成告警数据处理，告警名称: {exec_res.get('commonLabels', {}).get('alertname', 'N/A')}"
        )
        return "default"


class AnalyzeRootCauseNode(AsyncNode):
    """
    分析告警根因，并决定下一步要执行的 K8s 工具或宣布问题解决。
    """

    async def prep_async(self, shared):
        alert_info = shared.get("alert_info")
        # 历史执行记录，用于 LLM 决策
        history = shared.get("execution_history", [])
        if not alert_info:
            raise ValueError(
                "AnalyzeRootCauseNode: 共享存储中缺少'alert_info'数据"
            )
        print(f"🔎 分析准备: 准备分析告警 {alert_info.get('commonLabels', {}).get('alertname', 'N/A')}")
        return alert_info, history

    async def exec_async(self, inputs):
        alert_info, history = inputs
        print(f"🧠 根因分析: 正在深入分析告警根本原因...")

        # K8s 工具列表
        client = MCPClient()
        try:
            get_tools_description = await client.get_tools_description()
        finally:
            await client.cleanup()

        history_str = json.dumps(history, indent=2, ensure_ascii=False)

        prompt = f"""
# Kubernetes告警分析与决策助手

## 你的角色
你是一位经验丰富的Kubernetes集群运维专家，擅长分析和解决各类Kubernetes告警问题。你的目标是尽可能自动化解决问题，只有在确实无法自动修复的情况下才建议人工干预。

## 当前任务
我们收到了一个Kubernetes集群告警，需要你：
1. 分析告警的根本原因
2. 查看历史执行记录（如果有）
3. 决定下一步最佳操作方案

## 告警详情
```json
{json.dumps(alert_info, indent=2, ensure_ascii=False)}
```

## 历史执行记录
{history_str if history else "目前没有历史执行记录，这是首次处理该告警。"}

## 可用的Kubernetes工具
以下是你可以调用的Kubernetes工具，请根据需要选择合适的工具：
{get_tools_description}

## 决策要求
请分析上述信息，并做出以下三种决策之一：

1. **执行工具**：如果你认为可以通过执行某个工具来解决或进一步诊断问题
2. **问题已解决**：如果你认为问题已经解决，不需要进一步操作
3. **需要人工干预**：如果你认为问题超出了自动化处理能力，需要人工介入

## 输出格式
请使用YAML格式输出你的决策，必须包含以下字段：
- `decision`：决策类型，值必须是`execute_tool`、`resolved`或`needs_manual_intervention`之一
- `reason`：详细说明你做出此决策的原因（使用中文）

如果决策是`execute_tool`，还必须包含：
- `tool_call`：一个包含以下字段的对象：
  - `tool_name`：要执行的工具名称
  - `parameters`：工具所需的参数

### 示例1：执行工具
```yaml
reason: 根据告警信息，Pod内存使用率过高，需要查看Pod详细信息以确定问题原因
decision: execute_tool
tool_call:
  tool_name: kubectl_describe_pod
  parameters:
    namespace: default
    pod_name: my-app-pod-xyz
```

### 示例2：使用kubectl_patch工具
```yaml
reason: 需要更新Deployment的镜像版本以修复安全漏洞
decision: execute_tool
tool_call:
  tool_name: kubectl_patch
  parameters:
    resourceType: deployment
    name: nginx-deployment
    namespace: default
    patchType: strategic
    patchData:
      spec:
        template:
          spec:
            containers:
            - name: nginx
              image: nginx:1.25.3
```

### 示例3：问题已解决
```yaml
reason: 通过历史执行记录可以看到，Pod已成功重启并恢复正常运行，CPU使用率已降至正常水平
decision: resolved
```

### 示例4：需要人工干预
```yaml
reason: 多次尝试自动修复后问题仍然存在，可能需要检查应用代码或调整资源配置，建议人工介入处理
decision: needs_manual_intervention
```
"""
        messages = [{"role": "user", "content": prompt}]

        try:
            llm_response = call_llm(messages)
            print(f"📝 LLM响应: 已收到大模型分析结果")
            print(f"\n📄 LLM完整响应内容:\n{llm_response}\n")

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
                f"⚠️ 解析错误: LLM响应解析失败或决策无效: {e}"
            )
            # 如果解析失败，返回一个需要人工干预的决策
            return {
                "decision": "needs_manual_intervention",
                "reason": f"LLM决策解析失败: {e}",
            }

    async def post_async(self, shared, prep_res, exec_res):
        # exec_res 包含 LLM 的决策结果
        decision = exec_res["decision"]

        if decision == "execute_tool":
            shared["current_tool_call"] = exec_res["tool_call"]
            print(
                f"🛠️ 执行决策: 决定执行工具: {exec_res['tool_call']['tool_name']}"
            )
            return "execute_tool"
        elif decision == "resolved":
            shared["resolution_reason"] = exec_res["reason"]
            print(
                f"✅ 问题解决: 告警问题已解决。原因: {exec_res['reason']}"
            )
            return "resolved"
        elif decision == "needs_manual_intervention":
            shared["manual_intervention_reason"] = exec_res["reason"]
            print(
                f"👨‍💻 人工干预: 需要人工介入处理。原因: {exec_res['reason']}"
            )
            return "needs_manual_intervention"
        else:
            # 兜底，如果 LLM 返回了未知决策
            print(
                f"❓ 未知决策: LLM返回了未知决策类型: {decision}，默认转为人工干预"
            )
            shared["manual_intervention_reason"] = f"未知的LLM决策类型: {decision}"
            return "needs_manual_intervention"


class ExecuteSolutionNode(AsyncNode):
    """
    执行单个 K8s 工具调用的节点。
    """

    async def prep_async(self, shared):
        tool_call = shared.get("current_tool_call")
        if not tool_call:
            raise ValueError(
                "ExecuteSolutionNode: 共享存储中缺少'current_tool_call'数据"
            )
        return tool_call

    async def exec_async(self, tool_call):
        tool_name = tool_call.get("tool_name")
        parameters = tool_call.get("parameters", {})

        print(f"🔧 工具执行: 开始执行工具 {tool_name}，参数: {parameters}")
        try:
            client = MCPClient()
            try:
                result = await client.tool_execution(tool_name, parameters)
                print(f"📊 执行结果: 工具 {tool_name} 执行完成")
                print(f"\n📄 工具执行详细结果:\n{result}\n")
            finally:
                await client.cleanup()

            # 修复：result是字符串而不是字典，不能使用get方法
            return {
                "tool_call": tool_call,
                "success": "True",  
                "output": result,
            }
        except Exception as e:
            return {
                "tool_call": tool_call,
                "success": "False",
                "output": f"Exception during tool execution: {e}",
            }

    async def post_async(self, shared, prep_res, exec_res):
        # 将本次执行结果添加到历史记录中
        if "execution_history" not in shared:
            shared["execution_history"] = []
        shared["execution_history"].append(exec_res)

        success_status = "成功" if exec_res['success'] == "True" else "失败"
        print(
            f"📝 执行记录: 工具 {exec_res['tool_call']['tool_name']} 执行{success_status}，已添加到历史记录"
        )

        # 无论成功或失败，都返回到 AnalyzeRootCauseNode 进行下一次决策
        return "tool_executed"


class GenerateReportNode(AsyncNode):
    """
    生成告警处理报告的节点。
    将工具执行的原始信息和告警信息发送给大模型，让大模型整理生成真实的报告。
    """

    async def prep_async(self, shared):
        print("📊 报告准备: 开始收集告警处理数据，准备生成最终报告...")
        alert_info = shared.get("alert_info", {})
        execution_history = shared.get("execution_history", [])
        resolution_reason = shared.get("resolution_reason", "")
        manual_intervention_reason = shared.get("manual_intervention_reason", "")

        return {
            "alert_info": alert_info,
            "execution_history": execution_history,
            "resolution_reason": resolution_reason,
            "manual_intervention_reason": manual_intervention_reason,
        }

    async def exec_async(self, inputs):
        alert_info = inputs["alert_info"]
        execution_history = inputs["execution_history"]
        resolution_reason = inputs["resolution_reason"]
        manual_intervention_reason = inputs["manual_intervention_reason"]

        # 获取告警基本信息
        alert_name = alert_info.get("commonLabels", {}).get("alertname", "N/A")
        severity = alert_info.get("commonLabels", {}).get("severity", "N/A")
        instance = alert_info.get("commonLabels", {}).get("instance", "N/A")
        namespace = alert_info.get("commonLabels", {}).get("namespace", "N/A")
        starts_at = alert_info.get("startsAt", "N/A")
        description = alert_info.get("commonAnnotations", {}).get("description", "N/A")

        # 确定处理状态
        if resolution_reason:
            status = "已解决"
            status_reason = resolution_reason
        elif manual_intervention_reason:
            status = "需要人工干预"
            status_reason = manual_intervention_reason
        else:
            status = "处理中"
            status_reason = "自动处理进行中"

        # 构建提示词
        report_prompt = f"""
你是一个专业的Kubernetes告警处理报告生成器。请根据以下信息生成一份详细的告警处理报告，格式为Markdown。

## 告警信息
```json
{json.dumps(alert_info, indent=2, ensure_ascii=False)}
```

## 执行历史
```json
{json.dumps(execution_history, indent=2, ensure_ascii=False)}
```

## 处理状态
- 状态: {status}
- 原因: {status_reason}

请生成一份专业的告警处理报告，包括以下部分：
1. 告警概述：包括告警名称、严重性、实例、命名空间、告警时间等基本信息
2. 问题描述：根据告警信息描述问题
3. 处理过程：根据执行历史详细描述处理过程，包括执行的每个工具及其结果
4. 根因分析：根据执行结果分析问题的根本原因
5. 解决方案：如果问题已解决，描述解决方案；如果需要人工干预，给出建议
6. 总结：总结整个处理过程和结果

请确保报告内容准确、专业，并基于提供的真实数据。
"""

        messages = [{"role": "user", "content": report_prompt}]
        llm_response = call_llm(messages)
        # print(f"\n📄 报告生成LLM完整响应:\n{llm_response}\n")
        return llm_response

    async def post_async(self, shared, prep_res, exec_res):
        shared["report"] = exec_res
        print("📝 报告完成: 告警处理报告已生成并存储")
        print("\n🔍 --- 最终报告 --- 🔍\n")
        print(exec_res)
        print("\n🔍 ---------------- 🔍\n")
        return "default"
