"""Synchronous facade over MCP v1 SDK sessions; no connections on import."""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import anyio
import httpx
from dotenv import dotenv_values
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, model_validator

from research_agent.core.budget import BudgetExceeded, remaining_timeout
from research_agent.core.config import get_settings


class MCPServerConfig(BaseModel):
    transport: Literal['stdio', 'streamable-http', 'sse'] = 'stdio'
    enabled: bool = True
    command: str = ''
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ''
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=60, gt=0, le=900)
    allowed_tools: list[str] = Field(default_factory=list)
    read_only_tools: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_transport(self):
        if self.transport == 'stdio' and not self.command.strip():
            raise ValueError('stdio server requires command')
        if self.transport != 'stdio':
            parsed = urlsplit(self.url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('HTTP MCP server requires an HTTP/HTTPS URL without credentials')
        if not set(self.read_only_tools).issubset(self.allowed_tools):
            raise ValueError('read_only_tools must be a subset of allowed_tools')
        return self


class MCPClient:
    """Only local operator configuration can select commands, servers and tools."""
    def __init__(self, config_path: str | None = None):
        self.path = Path(config_path or get_settings().mcp_config_path)
        self.servers: dict[str, MCPServerConfig] = {}
        self._secrets: set[str] = set()
        if self.path.exists():
            try:
                if self.path.stat().st_size > 256 * 1024:
                    raise ValueError('config too large')
                raw = json.loads(self.path.read_text(encoding='utf-8-sig'))
                configs = raw.get('mcpServers', {})
                if not isinstance(configs, dict) or len(configs) > 20:
                    raise ValueError('invalid server map')
                for name, config in configs.items():
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
                        raise ValueError('invalid server name')
                    self.servers[name] = MCPServerConfig.model_validate(config)
            except Exception as exc:
                # ValidationError may contain plaintext header/env values.
                raise ValueError('MCP 配置无效，请检查本地 JSON、传输类型和工具许可字段') from None

    def _expand(self, value: str) -> str:
        local = dotenv_values('.env')
        def replace(match):
            key = match.group(1)
            resolved = os.environ.get(key, local.get(key))
            if resolved is None:
                raise ValueError(f'MCP 所需环境变量 {key} 未配置')
            if resolved:
                self._secrets.add(resolved)
            return resolved
        return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', replace, value)

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in sorted(self._secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        return value

    def configured(self) -> list[dict]:
        return [{'name': name, 'transport': server.transport, 'enabled': server.enabled,
                 'allowed_tools': server.allowed_tools, 'read_only_tools': server.read_only_tools}
                for name, server in self.servers.items()]

    def _server(self, name):
        server = self.servers.get(name)
        if server is None or not server.enabled:
            raise ValueError('MCP Server 未配置或已禁用')
        return server

    @asynccontextmanager
    async def _session(self, server: MCPServerConfig):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client
        timeout = remaining_timeout(server.timeout)
        # Timeout covers initialization, pagination and call, not just one HTTP request.
        with anyio.fail_after(timeout):
            async with AsyncExitStack() as stack:
                if server.transport == 'stdio':
                    env = {key: self._expand(value) for key, value in server.env.items()}
                    self._secrets.update(value for value in env.values() if value)
                    params = StdioServerParameters(command=server.command, args=server.args,
                        env=env, cwd=server.cwd)
                    # Server stderr can contain credentials; do not forward it to API/Worker logs.
                    errors = stack.enter_context(open(os.devnull, 'w', encoding='utf-8'))
                    streams = await stack.enter_async_context(stdio_client(params, errlog=errors))
                    async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        await session.initialize()
                        yield session
                else:
                    headers = {key: self._expand(value) for key, value in server.headers.items()}
                    self._secrets.update(value for value in headers.values() if value)
                    self._secrets.update(value[7:] for value in headers.values() if value.lower().startswith('bearer ') and value[7:])
                    if server.transport == 'sse':
                        streams = await stack.enter_async_context(sse_client(server.url, headers=headers,
                            timeout=timeout, sse_read_timeout=timeout))
                    else:
                        client = await stack.enter_async_context(httpx.AsyncClient(
                            headers=headers, timeout=timeout, follow_redirects=False))
                        streams = await stack.enter_async_context(streamable_http_client(server.url, http_client=client))
                    async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        await session.initialize()
                        yield session

    async def _tools(self, session):
        tools, cursor, seen = [], None, set()
        for _ in range(50):
            page = await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            if len(tools) > 500:
                raise ValueError('MCP 工具目录超过 500 个上限')
            cursor = page.nextCursor
            if not cursor:
                return tools
            if cursor in seen:
                raise ValueError('MCP Server 返回重复分页游标')
            seen.add(cursor)
        raise ValueError('MCP 工具目录分页超过上限')

    async def _discover(self, names):
        tools, errors = {}, []
        for name in names:
            server = self._server(name)
            try:
                async with self._session(server) as session:
                    for tool in await self._tools(session):
                        qualified = f'mcp:{name}:{tool.name}'
                        tools[qualified] = {'description': tool.description or '',
                            'input_schema': tool.inputSchema, 'server': name, 'tool': tool.name,
                            'allowed': tool.name in server.allowed_tools,
                            'danger_level': 'safe' if tool.name in server.read_only_tools else 'sensitive'}
            except BudgetExceeded:
                raise
            except Exception as exc:
                errors.append({'server': name, 'error': self.redact(f'{type(exc).__name__}: {exc}')})
        return {'tools': self.redact(tools), 'errors': errors}

    def discover(self, server: str | None = None) -> dict:
        if server:
            self._server(server)
        names = [server] if server else [name for name, item in self.servers.items() if item.enabled]
        return asyncio.run(self._discover(names))

    async def _call(self, name: str, arguments: dict, authorized: bool):
        parts = name.split(':', 2)
        if len(parts) != 3 or parts[0] != 'mcp':
            raise ValueError('MCP 工具名称须为 mcp:server:tool')
        _, server_name, tool_name = parts
        server = self._server(server_name)
        if tool_name not in server.allowed_tools:
            raise PermissionError('工具不在本地配置的 allowed_tools 中')
        if tool_name not in server.read_only_tools and not authorized:
            raise PermissionError('MCP 写入/未知权限工具需要当前任务显式授权')
        async with self._session(server) as session:
            tool = next((tool for tool in await self._tools(session) if tool.name == tool_name), None)
            if tool is None:
                raise ValueError('MCP Server 未返回该工具')
            Draft202012Validator.check_schema(tool.inputSchema)
            validation = next(Draft202012Validator(tool.inputSchema).iter_errors(arguments), None)
            if validation:
                raise ValueError('MCP 参数不符合工具 inputSchema：' + validation.message)
            result = await session.call_tool(tool_name, arguments=arguments)
        content = []
        for block in result.content:
            item = block.model_dump(mode='json', by_alias=True, exclude_none=True)
            # Binary image/audio/resource blocks are not blindly put into the text model context.
            if item.get('type') in {'image', 'audio'}:
                item.pop('data', None)
                item['omitted_binary'] = True
            resource = item.get('resource')
            if isinstance(resource, dict) and 'blob' in resource:
                resource.pop('blob')
                resource['omitted_binary'] = True
            content.append(item)
        observation = {'tool_used': name, 'documents': [], 'content': content,
            'structured_content': result.structuredContent, 'is_error': result.isError,
            'error': 'MCP 工具返回错误，请查看 content' if result.isError else None}
        raw = json.dumps(self.redact(observation), ensure_ascii=False)
        limit = get_settings().mcp_max_result_chars
        if len(raw) > limit:
            return {'tool_used': name, 'documents': [], 'content': raw[:limit],
                    'truncated': True, 'error': observation['error']}
        return self.redact(observation)

    def call(self, name: str, arguments: dict, authorized: bool = False) -> dict:
        try:
            return asyncio.run(self._call(name, arguments, authorized))
        except BudgetExceeded:
            raise
        except Exception as exc:
            return {'tool_used': name, 'documents': [], 'error': self.redact(f'{type(exc).__name__}: {exc}')}
