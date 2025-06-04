import asyncio
import json
import logging
import os
import shutil
from contextlib import AsyncExitStack
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Configuration:
    """Manages configuration and environment variables for the MCP client."""


    @staticmethod
    def load_config(file_path: str) -> dict[str, Any]:
        """Load server configuration from JSON file.

        Args:
            file_path: Path to the JSON configuration file.

        Returns:
            Dict containing server configuration.

        Raises:
            FileNotFoundError: If configuration file doesn't exist.
            JSONDecodeError: If configuration file is invalid JSON.
        """
        with open(file_path, "r") as f:
            return json.load(f)


class Server:
    """Manages MCP server connections and tool execution."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name: str = name
        self.config: dict[str, Any] = config
        self.stdio_context: Any | None = None
        self.session: ClientSession | None = None
        self._cleanup_lock: asyncio.Lock = asyncio.Lock()
        self.exit_stack: AsyncExitStack = AsyncExitStack()

    async def initialize(self) -> None:
        """Initialize the server connection."""
        command = (
            shutil.which("npx")
            if self.config["command"] == "npx"
            else self.config["command"]
        )
        if command is None:
            raise ValueError("The command must be a valid string and cannot be None.")

        server_params = StdioServerParameters(
            command=command,
            args=self.config["args"],
            env=(
                {**os.environ, **self.config["env"]} if self.config.get("env") else None
            ),
        )
        try:
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self.session = session
        except Exception as e:
            logging.error(f"Error initializing server {self.name}: {e}")
            await self.cleanup()
            raise

    async def list_tools(self) -> list[Any]:
        """List available tools from the server.

        Returns:
            A list of available tools.

        Raises:
            RuntimeError: If the server is not initialized.
        """
        if not self.session:
            raise RuntimeError(f"Server {self.name} not initialized")

        tools_response = await self.session.list_tools()
        tools = []

        for item in tools_response:
            if isinstance(item, tuple) and item[0] == "tools":
                tools.extend(
                    Tool(tool.name, tool.description, tool.inputSchema)
                    for tool in item[1]
                )

        return tools

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        retries: int = 2,
        delay: float = 1.0,
    ) -> Any:
        """Execute a tool with retry mechanism.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.
            retries: Number of retry attempts.
            delay: Delay between retries in seconds.

        Returns:
            Tool execution result.

        Raises:
            RuntimeError: If server is not initialized.
            Exception: If tool execution fails after all retries.
        """
        if not self.session:
            raise RuntimeError(f"Server {self.name} not initialized")

        attempt = 0
        while attempt < retries:
            try:
                logging.info(f"Executing {tool_name}...")
                result = await self.session.call_tool(tool_name, arguments)

                return result

            except Exception as e:
                attempt += 1
                logging.warning(
                    f"Error executing tool: {e}. Attempt {attempt} of {retries}."
                )
                if attempt < retries:
                    logging.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logging.error("Max retries reached. Failing.")
                    raise

    async def cleanup(self) -> None:
        """Clean up server resources."""
        async with self._cleanup_lock:
            try:
                await self.exit_stack.aclose()
                self.session = None
                self.stdio_context = None
            except Exception as e:
                logging.error(f"Error during cleanup of server {self.name}: {e}")


class Tool:
    """Represents a tool with its properties and formatting."""

    def __init__(
        self, name: str, description: str, input_schema: dict[str, Any]
    ) -> None:
        self.name: str = name
        self.description: str = description
        self.input_schema: dict[str, Any] = input_schema

    def format_for_llm(self) -> str:
        """Format tool information for LLM.

        Returns:
            A formatted string describing the tool.
        """
        args_desc = []
        if "properties" in self.input_schema:
            for param_name, param_info in self.input_schema["properties"].items():
                arg_desc = (
                    f"- {param_name}: {param_info.get('description', 'No description')}"
                )
                if param_name in self.input_schema.get("required", []):
                    arg_desc += " (required)"
                args_desc.append(arg_desc)

        return f"""
Tool: {self.name}
Description: {self.description}
Arguments:
{chr(10).join(args_desc)}
"""


from abc import ABC, abstractmethod


class AbstractToolClient(ABC):
    """Abstract base class for tool clients."""

    @abstractmethod
    async def get_tools_description(self) -> str:
        """Abstract method to get descriptions of available tools."""
        pass

    @abstractmethod
    async def tool_execution(self, tool_name: str, arguments: dict) -> str:
        """Abstract method to execute a tool."""
        pass


class ToolSession(AbstractToolClient):
    """Orchestrates the interaction between user, LLM, and tools."""

    def __init__(self, servers: list[Server]) -> None:
        self.servers: list[Server] = servers

    async def cleanup_servers(self) -> None:
        """Clean up all servers properly."""
        for server in reversed(self.servers):
            try:
                await server.cleanup()
            except Exception as e:
                logging.warning(f"Warning during final cleanup: {e}")

    async def tool_execution(self, tool_name: str, arguments: dict) -> str:
        """Process the LLM response and execute tools if needed.


        Returns:
            The result of tool execution.
        """
        for server in self.servers:
            try:
                await server.initialize()
            except Exception as e:
                logging.error(f"Failed to initialize server: {e}")
                await self.cleanup_servers()
                return "Error: Failed to initialize server."

        for server in self.servers:
            tools = await server.list_tools()
            if any(tool.name == tool_name for tool in tools):
                try:
                    result = await server.execute_tool(
                        tool_name, arguments
                    )

                    if isinstance(result, dict) and "progress" in result:
                        progress = result["progress"]
                        total = result["total"]
                        percentage = (progress / total) * 100
                        logging.info(
                            f"Progress: {progress}/{total} ({percentage:.1f}%)"
                        )

                    return f"Tool execution result: {result}"
                except Exception as e:
                    error_msg = f"Error executing tool: {str(e)}"
                    logging.error(error_msg)
                    return error_msg

        return f"No server found with tool: {tool_name}"

    async def get_tools_description(self) -> str:
        """ """
        try:
            for server in self.servers:
                try:
                    await server.initialize()
                except Exception as e:
                    logging.error(f"Failed to initialize server: {e}")
                    await self.cleanup_servers()
                    return "Error: Failed to initialize server."

            all_tools = []
            for server in self.servers:
                tools = await server.list_tools()
                all_tools.extend(tools)

            tools_description = "\n".join([tool.format_for_llm() for tool in all_tools])
            return tools_description

        except Exception as e:
            logging.error(f"Error getting tools description: {e}")
            return ""

class MCPClient(AbstractToolClient):
    """Client for interacting with MCP tools."""

    def __init__(self, config_file: str = "servers_config.json") -> None:
        self.config_file = config_file
        self.toolsession: ToolSession | None = None

    async def _initialize_toolsession(self) -> None:
        if self.toolsession is None:
            config = Configuration()
            server_config = config.load_config(self.config_file)
            servers = [
                Server(name, srv_config)
                for name, srv_config in server_config["mcpServers"].items()
            ]
            self.toolsession = ToolSession(servers)

    async def get_tools_description(self) -> str:
        await self._initialize_toolsession()
        if self.toolsession:
            return await self.toolsession.get_tools_description()
        return "Error: ToolSession not initialized."

    async def tool_execution(self, tool_name: str, arguments: dict) -> str:
        await self._initialize_toolsession()
        if self.toolsession:
            return await self.toolsession.tool_execution(tool_name, arguments)
        return "Error: ToolSession not initialized."

    async def cleanup(self) -> None:
        if self.toolsession:
            await self.toolsession.cleanup_servers()


async def main() -> None:
    """Initialize and run the chat session."""
    client = MCPClient()
    try:
        get_tools_description = await client.get_tools_description()
        print(f"get_tools_description:\n{get_tools_description}")
        # result = await client.tool_execution("kubectl_get", {"resourceType": "pod", "output": "yaml"})
        result = await client.tool_execution("kubectl_describe", {'resourceType': 'pod', 'name': 'nginx-deployment-85c88b48cd-6kmxz', 'namespace': 'default'})
        print(f"result:\n{result}")
        time.sleep(1)
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
