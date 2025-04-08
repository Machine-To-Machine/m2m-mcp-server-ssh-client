"""
Create an MCP server that proxies requests through an MCP client.

This server is created independent of any transport mechanism and acts
as a transparent proxy for remote MCP servers.

This code is sourced from: https://github.com/sparfenyuk/mcp-proxy and modified
to work with our SSH client implementation.

Original author: Sergey Parfenyuk
"""

import typing as t

from mcp import server, types
from mcp.client.session import ClientSession


async def create_proxy_server(
    remote_app: ClientSession,
) -> server.Server[object]:  # noqa: C901
    """
    Create a server instance that proxies to a remote MCP server.

    This function creates a proxy MCP server that forwards all requests to a
    remote MCP server. The proxy server mirrors the capabilities of the remote
    server, including tools, prompts, and resources.

    Args:
        remote_app: Client session connected to the remote MCP server

    Returns:
        A server instance that proxies requests to the remote server
    """
    response = await remote_app.initialize()
    capabilities = response.capabilities

    app: server.Server[object] = server.Server(name=response.serverInfo.name)

    if capabilities.prompts:

        async def _list_prompts(_: t.Any) -> types.ServerResult:  # noqa: ANN401
            """
            List available prompts from the remote server.

            Args:
                _: Request object (unused)

            Returns:
                ServerResult containing the list of prompts
            """
            result = await remote_app.list_prompts()
            return types.ServerResult(result)

        app.request_handlers[types.ListPromptsRequest] = _list_prompts

        async def _get_prompt(req: types.GetPromptRequest) -> types.ServerResult:
            """
            Get a specific prompt from the remote server.

            Args:
                req: Request containing the prompt name and arguments

            Returns:
                ServerResult containing the prompt
            """
            result = await remote_app.get_prompt(req.params.name, req.params.arguments)
            return types.ServerResult(result)

        app.request_handlers[types.GetPromptRequest] = _get_prompt

    if capabilities.resources:

        async def _list_resources(_: t.Any) -> types.ServerResult:  # noqa: ANN401
            """
            List available resources from the remote server.

            Args:
                _: Request object (unused)

            Returns:
                ServerResult containing the list of resources
            """
            result = await remote_app.list_resources()
            return types.ServerResult(result)

        app.request_handlers[types.ListResourcesRequest] = _list_resources

        # list_resource_templates() is not implemented in the client
        # async def _list_resource_templates(_: t.Any) -> types.ServerResult:
        #     result = await remote_app.list_resource_templates()
        #     return types.ServerResult(result)

        # app.request_handlers[types.ListResourceTemplatesRequest] = \
        # _list_resource_templates

        async def _read_resource(
            req: types.ReadResourceRequest,
        ) -> types.ServerResult:
            """
            Read a specific resource from the remote server.

            Args:
                req: Request containing the resource URI

            Returns:
                ServerResult containing the resource content
            """
            result = await remote_app.read_resource(req.params.uri)
            return types.ServerResult(result)

        app.request_handlers[types.ReadResourceRequest] = _read_resource

    if capabilities.logging:

        async def _set_logging_level(
            req: types.SetLevelRequest,
        ) -> types.ServerResult:
            """
            Set logging level on the remote server.

            Args:
                req: Request containing the logging level

            Returns:
                Empty ServerResult
            """
            await remote_app.set_logging_level(req.params.level)
            return types.ServerResult(types.EmptyResult())

        app.request_handlers[types.SetLevelRequest] = _set_logging_level

    if capabilities.resources:

        async def _subscribe_resource(
            req: types.SubscribeRequest,
        ) -> types.ServerResult:
            """
            Subscribe to a resource on the remote server.

            Args:
                req: Request containing the resource URI

            Returns:
                Empty ServerResult
            """
            await remote_app.subscribe_resource(req.params.uri)
            return types.ServerResult(types.EmptyResult())

        app.request_handlers[types.SubscribeRequest] = _subscribe_resource

        async def _unsubscribe_resource(
            req: types.UnsubscribeRequest,
        ) -> types.ServerResult:
            """
            Unsubscribe from a resource on the remote server.

            Args:
                req: Request containing the resource URI

            Returns:
                Empty ServerResult
            """
            await remote_app.unsubscribe_resource(req.params.uri)
            return types.ServerResult(types.EmptyResult())

        app.request_handlers[types.UnsubscribeRequest] = _unsubscribe_resource

    if capabilities.tools:

        async def _list_tools(_: t.Any) -> types.ServerResult:  # noqa: ANN401
            """
            List available tools from the remote server.

            Args:
                _: Request object (unused)

            Returns:
                ServerResult containing the list of tools
            """
            tools = await remote_app.list_tools()
            return types.ServerResult(tools)

        app.request_handlers[types.ListToolsRequest] = _list_tools

        async def _call_tool(req: types.CallToolRequest) -> types.ServerResult:
            """
            Call a specific tool on the remote server.

            Args:
                req: Request containing the tool name and arguments

            Returns:
                ServerResult containing the tool execution result or error
            """
            try:
                result = await remote_app.call_tool(
                    req.params.name,
                    (req.params.arguments or {}),
                )
                return types.ServerResult(result)
            except ValueError as e:
                # Invalid argument or value error
                return types.ServerResult(
                    types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text=f"Invalid argument: {str(e)}"
                            )
                        ],
                        isError=True,
                    ),
                )
            except KeyError as e:
                # Missing key error
                return types.ServerResult(
                    types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text",
                                text=f"Missing required parameter: {str(e)}",
                            )
                        ],
                        isError=True,
                    ),
                )
            except Exception as e:
                # Generic error fallback
                return types.ServerResult(
                    types.CallToolResult(
                        content=[types.TextContent(type="text", text=str(e))],
                        isError=True,
                    ),
                )

        app.request_handlers[types.CallToolRequest] = _call_tool

    async def _send_progress_notification(
        req: types.ProgressNotification,
    ) -> None:
        """
        Forward progress notification to the remote server.

        Args:
            req: Notification containing progress information
        """
        await remote_app.send_progress_notification(
            req.params.progressToken,
            req.params.progress,
            req.params.total,
        )

    app.notification_handlers[types.ProgressNotification] = _send_progress_notification

    async def _complete(req: types.CompleteRequest) -> types.ServerResult:
        """
        Forward completion request to the remote server.

        Args:
            req: Request containing completion reference and argument

        Returns:
            ServerResult containing the completion result
        """
        result = await remote_app.complete(
            req.params.ref,
            req.params.argument.model_dump(),
        )
        return types.ServerResult(result)

    app.request_handlers[types.CompleteRequest] = _complete

    return app
