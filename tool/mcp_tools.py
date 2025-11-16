"""
Modern MCP Integration (LangChain 2025)
Using MultiServerMCPClient with GitHub MCP Server
"""

import os
from langchain_mcp_adapters.client import MultiServerMCPClient


def _clean_schema(schema):
    """Remove unsupported JSON Schema fields that cause warnings."""
    if isinstance(schema, dict):
        # Remove problematic fields
        schema.pop('$schema', None)
        schema.pop('additionalProperties', None)
        
        # Recursively clean nested schemas
        for key, value in list(schema.items()):
            if isinstance(value, dict):
                _clean_schema(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _clean_schema(item)
    return schema


async def get_mcp_tools():
    """
    Load GitHub MCP tools via MultiServerMCPClient.
    
    GitHub MCP Server provides 26 tools including:
      - create_repository, search_repositories, fork_repository
      - create_or_update_file, get_file_contents, push_files
      - create_issue, create_pull_request, create_branch
    """
    github_token = os.getenv("GITHUB_TOKEN")
    
    if not github_token:
        print("Warning: GITHUB_TOKEN not found")
        return []
    
    try:
        client = MultiServerMCPClient({
            "github": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "GITHUB_TOKEN": github_token,
                    "GITHUB_PERSONAL_ACCESS_TOKEN": github_token
                }
            }
        })
        
        mcp_tools = await client.get_tools()
        
        # Clean tool schemas to remove unsupported fields
        for tool in mcp_tools:
            if hasattr(tool, 'args_schema') and tool.args_schema:
                _clean_schema(tool.args_schema)
        
        print(f"Loaded {len(mcp_tools)} GitHub MCP tools")
        return mcp_tools
    
    except Exception as e:
        print(f"Warning: Could not load MCP tools: {e}")
        return []
