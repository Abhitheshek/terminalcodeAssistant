# -*- coding: utf-8 -*-
"""Simple GitHub Agent - MCP Integration"""
import asyncio
import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

async def main():
    """MCP-based GitHub agent"""
    
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("Error: GITHUB_TOKEN not found")
        return
    
    print("Initializing MCP client...")
    
    # Set token for env inheritance
    os.environ["GITHUB_TOKEN"] = github_token
    
    # Initialize MCP client
    client = MultiServerMCPClient({
        "github": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"]
        }
    })
    
    # Get tools
    mcp_tools = await client.get_tools()
    print(f"Loaded {len(mcp_tools)} MCP tools")
    for tool in mcp_tools[:5]:
        print(f"  - {tool.name}")
    
    # Initialize LLM
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0
    )
    
    # Bind tools
    llm_with_tools = llm.bind_tools(mcp_tools)
    
    print("\nGitHub Agent Ready (MCP)!")
    print("Commands: create repo <name> | list repos | exit\n")
    
    while True:
        user_input = input("> ").strip()
        
        if user_input.lower() in ['exit', 'quit']:
            break
        
        # Invoke LLM
        response = llm_with_tools.invoke([
            {"role": "system", "content": "You are a GitHub assistant. Use MCP tools for all GitHub operations."},
            {"role": "user", "content": user_input}
        ])
        
        # Check for tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tool_call in response.tool_calls:
                print(f"\nTool: {tool_call['name']}")
                print(f"Args: {tool_call['args']}")
                
                # Find and execute tool
                tool = next((t for t in mcp_tools if t.name == tool_call['name']), None)
                if tool:
                    result = await tool.ainvoke(tool_call['args'])
                    print(f"Result: {result}")
        else:
            print(f"\n{response.content}")

if __name__ == "__main__":
    asyncio.run(main())
