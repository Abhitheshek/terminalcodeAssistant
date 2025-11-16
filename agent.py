"""
Core Agent Implementation using LangGraph and MCP
"""
import os
import asyncio
from typing import Annotated, Sequence, Literal

from pydantic import BaseModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.tree import Tree
from rich.prompt import Prompt
from tool.local_tools import get_local_tools
from tool.mcp_tools import get_mcp_tools
from tool.github_direct import get_github_tools



console = Console()




class AgentState(BaseModel):
    """State management for the agent workflow"""
    messages: Annotated[Sequence[BaseMessage], add_messages]


class CodeAssistantAgent:
    """
    Minimalist AI Coding Assistant using LangGraph and MCP
    
    Architecture:
    - StateGraph with 3 nodes: user_input, model_response, tool_use
    - Persistent state using SQLite checkpointing
    - Tool integration: local tools + MCP servers
    """
    
    def __init__(self):
        self.console = console
        self._checkpointer_ctx = None
        self.checkpointer = None
        self.agent = None
        self.thread_id = "default_session"
        self.last_options = {}  # Store numbered options from bullet points
        self.mcp_tools = []  # Initialize mcp_tools attribute
        
        # Display welcome banner
        self._display_welcome()
        
        # Initialize LLM with rate limit handling
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_tokens=2048,
            max_retries=3,
            timeout=60,
        )
        
        # Initialize tools
        self.console.print("[cyan]Loading tools...[/cyan]")
        self.tools = []
        
        # Load local tools
        local_tools = get_local_tools()
        self.tools.extend(local_tools)
        self.console.print(f"[green]Loaded {len(local_tools)} local tools[/green]")
        
        # Load direct GitHub tools
        github_tools = get_github_tools()
        self.tools.extend(github_tools)
        self.console.print(f"[green]Loaded {len(github_tools)} GitHub tools[/green]")
        
        # Build workflow
        self.workflow = StateGraph(AgentState)
        self._setup_workflow()
    
    def _display_welcome(self):
        """Display welcome banner"""
        banner = """
=============================================================

   CODE ASSISTANT - Powered by LangGraph + MCP
   
   Type 'exit' or 'quit' to terminate

=============================================================
        """
        self.console.print(banner, style="bold cyan")
    
    def _setup_workflow(self):
        """Setup the StateGraph workflow"""
        # Register nodes
        self.workflow.add_node("model_response", self.model_response)
        self.workflow.add_node("tool_use", self.tool_use)
        
        # Define edges
        self.workflow.set_entry_point("model_response")
        self.workflow.add_edge("tool_use", "model_response")
        
        # Conditional routing
        self.workflow.add_conditional_edges(
            "model_response",
            self.check_tool_use,
            {
                "tool_use": "tool_use",
                END: END,
            },
        )
    
    async def initialize(self):
        """Async initialization for checkpointer and MCP tools"""
        # Initialize SQLite checkpointer
        db_path = os.path.join(os.getcwd(), "checkpoints.db")
        self.console.print(f"[cyan]Initializing checkpoint database: {db_path}[/cyan]")
        
        self._checkpointer_ctx = AsyncSqliteSaver.from_conn_string(db_path)
        self.checkpointer = await self._checkpointer_ctx.__aenter__()
        
        # Load MCP tools
        try:
            self.mcp_tools = await get_mcp_tools()
            if self.mcp_tools:
                self.tools.extend(self.mcp_tools)
                self.console.print(f"[green]Loaded {len(self.mcp_tools)} GitHub MCP tools[/green]")
        except Exception as e:
            self.console.print(f"[yellow]Warning: Could not load MCP tools: {e}[/yellow]")
        
        # Bind all tools
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        
        # Compile the workflow with recursion limit
        self.agent = self.workflow.compile(
            checkpointer=self.checkpointer
        )
        self.console.print("[green]Agent initialized successfully![/green]\n")
        
        # Show quick start guide
        self._display_quick_start()
    
    async def cleanup(self):
        """Cleanup resources"""
        try:
            if self._checkpointer_ctx:
                await self._checkpointer_ctx.__aexit__(None, None, None)
        except Exception:
            pass  # Ignore cleanup errors on exit
    
    def _format_with_numbers(self, text) -> str:
        """Convert bullet points to numbered list and store mapping"""
        import re
        
        # Handle if text is a list of content blocks
        if isinstance(text, list):
            # Extract text from content blocks
            text_parts = []
            for block in text:
                if hasattr(block, 'text'):
                    text_parts.append(block.text)
                elif isinstance(block, dict) and 'text' in block:
                    text_parts.append(block['text'])
                elif isinstance(block, str):
                    text_parts.append(block)
            text = '\n'.join(text_parts)
        
        # Ensure text is a string
        if not isinstance(text, str):
            text = str(text)
        
        # Reset options
        self.last_options = {}
        
        # Find bullet points (• or -)
        lines = text.split('\n')
        option_num = 1
        formatted_lines = []
        
        for line in lines:
            # Match bullet points with various formats
            bullet_match = re.match(r'^(\s*)[•\-\*]\s+(.+)$', line)
            if bullet_match:
                indent = bullet_match.group(1)
                content = bullet_match.group(2)
                
                # Store the mapping
                self.last_options[str(option_num)] = content
                
                # Replace with number
                formatted_lines.append(f"{indent}**{option_num}.** {content}")
                option_num += 1
            else:
                formatted_lines.append(line)
        
        result = '\n'.join(formatted_lines)
        
        # Add helper text if options were found
        if self.last_options:
            result += f"\n\n*Tip: Type a number (1-{len(self.last_options)}) to select an option*"
        
        return result
    
    def model_response(self, state: AgentState) -> dict:
        """Node: Generate model response"""
        messages = state.messages
        
        # Always add system message with tool instructions
        system_message = SystemMessage(content="""You MUST call tools. NEVER just talk. For 'push folder': call push_folder(owner, repo, folder_path, branch, message). For 'push file': call quick_push_file(owner, repo, file_path, branch, message). DO NOT ask questions, CALL THE TOOL NOW.""")
        
        if len(messages) == 1:
            messages = [system_message] + list(messages)
        else:
            # Replace first message if it's a system message
            if isinstance(messages[0], SystemMessage):
                messages[0] = system_message
            else:
                messages = [system_message] + list(messages)
        
        # Display thinking indicator
        with self.console.status("[bold cyan]Thinking...", spinner="dots"):
            # Check if user wants GitHub action - force tool use
            last_msg = messages[-1].content if hasattr(messages[-1], 'content') else ""
            if any(word in last_msg.lower() for word in ['push', 'upload', 'github', 'repo']):
                # Add explicit instruction to use tool
                messages[-1] = HumanMessage(content=f"{last_msg}\n\n[SYSTEM: You MUST call the appropriate tool NOW. Do not respond with text.]")
            
            response = self.llm_with_tools.invoke(messages)
        
        # Display AI response
        if response.content:
            # Convert bullet points to numbered list
            formatted_content = self._format_with_numbers(response.content)
            
            self.console.print(Panel(
                Markdown(formatted_content),
                title="[bold cyan]Assistant[/bold cyan]",
                border_style="cyan"
            ))
        
        return {"messages": [response]}
    
    async def tool_use(self, state: AgentState) -> dict:
        """Node: Execute tool calls"""
        import sys
        from io import StringIO
        
        messages = state.messages
        last_message = messages[-1]
        
        tool_calls = last_message.tool_calls
        tool_messages = []
        
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            
            # Display tool execution
            self.console.print(f"\n[bold yellow]Executing tool:[/bold yellow] [magenta]{tool_name}[/magenta]")
            self.console.print(f"[dim]Arguments: {tool_args}[/dim]\n")
            
            # Find and execute the tool
            tool = next((t for t in self.tools if t.name == tool_name), None)
            
            if tool:
                try:
                    # Suppress MCP stderr warnings during tool execution
                    old_stderr = sys.stderr
                    sys.stderr = StringIO()
                    
                    try:
                        # MCP tools are always async
                        if tool in self.mcp_tools:
                            result = await tool.ainvoke(tool_args)
                        else:
                            result = tool.invoke(tool_args)
                    finally:
                        sys.stderr = old_stderr
                    
                    # Display tool result
                    self.console.print(Panel(
                        str(result),
                        title=f"[bold green]Tool Result: {tool_name}[/bold green]",
                        border_style="green"
                    ))
                    
                    tool_messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call["id"]
                        )
                    )
                except Exception as e:
                    error_msg = f"Tool error: {str(e)}"
                    self.console.print(f"[bold red]{error_msg}[/bold red]")
                    
                    tool_messages.append(
                        ToolMessage(
                            content=error_msg,
                            tool_call_id=tool_call["id"]
                        )
                    )
            else:
                error_msg = f"Tool {tool_name} not found"
                self.console.print(f"[bold red]{error_msg}[/bold red]")
                
                tool_messages.append(
                    ToolMessage(
                        content=error_msg,
                        tool_call_id=tool_call["id"]
                    )
                )
        
        return {"messages": tool_messages}
    
    def check_tool_use(self, state: AgentState) -> Literal["tool_use", END]:
        """Conditional edge: Check if tools should be used"""
        messages = state.messages
        last_message = messages[-1]
        
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tool_use"
        return END
    
    async def run(self):
        """Main interactive loop"""
        config = {"configurable": {"thread_id": self.thread_id}}
        
        while True:
            try:
                # Get user input with rich prompt
                self.console.print()
                try:
                    user_input = Prompt.ask(
                        "[bold green]Your request[/bold green] [dim](or type 'help')[/dim]",
                        default=""
                    )
                except (EOFError, KeyboardInterrupt):
                    self.console.print("\n[bold cyan]Goodbye![/bold cyan]\n")
                    break
                
                # Skip empty input
                if not user_input or not user_input.strip():
                    continue
                
                # Check for exit commands
                if user_input.lower() in ['exit', 'quit', 'q']:
                    self.console.print("\n[bold cyan]Goodbye![/bold cyan]\n")
                    break
                
                # Special commands
                if user_input.lower() == 'help':
                    self._display_help()
                    continue
                
                if user_input.lower() == 'tools':
                    self._display_tools()
                    continue
                
                # Check if user typed a number to select an option
                if user_input.strip().isdigit() and user_input.strip() in self.last_options:
                    selected_option = self.last_options[user_input.strip()]
                    self.console.print(f"[green]Selected:[/green] {selected_option}\n")
                    user_input = selected_option
                
                # Direct command handler for GitHub push
                if 'push' in user_input.lower() and 'folder' in user_input.lower():
                    # Extract info and call tool directly
                    from tool.github_direct import push_folder
                    
                    # Get folder path from current directory
                    folder_name = 'codeAssistent' if 'codeassistent' in user_input.lower() else '.'
                    folder_path = os.path.join(os.getcwd(), folder_name) if folder_name != '.' else os.getcwd()
                    
                    self.console.print(f"\n[bold yellow]Executing:[/bold yellow] push_folder")
                    self.console.print(f"[dim]Folder: {folder_path}[/dim]\n")
                    
                    with self.console.status("[bold cyan]Pushing to GitHub...", spinner="dots"):
                        result = push_folder.invoke({
                            'owner': 'Abhitheshek',
                            'repo': 'terminalcodeAssistant',
                            'folder_path': folder_path,
                            'branch': 'main',
                            'message': 'Upload codeAssistent folder'
                        })
                    
                    self.console.print(Panel(
                        str(result),
                        title="[bold green]Result[/bold green]",
                        border_style="green"
                    ))
                    continue
                
                # Create human message
                human_message = HumanMessage(content=user_input)
                
                # Invoke the workflow (it will run until END)
                await self.agent.ainvoke(
                    {"messages": [human_message]},
                    config=config
                )
                
            except KeyboardInterrupt:
                self.console.print("\n[bold cyan]Goodbye![/bold cyan]\n")
                break
            except Exception as e:
                import traceback
                self.console.print(f"[bold red]Error: {e}[/bold red]")
                self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
                self.console.print("[yellow]Continuing... Type 'exit' to quit[/yellow]")
    
    def _display_quick_start(self):
        """Display quick start guide on startup"""
        # Set up initial numbered options
        self.last_options = {
            "1": "List all files in current directory",
            "2": "Show available tools",
            "3": "Run the tests",
            "4": "Read the README.md file",
            "5": "Search for code in the project"
        }
        
        quick_start = """
[bold cyan]What would you like to do?[/bold cyan]

**1.** List all files in current directory
**2.** Show available tools
**3.** Run the tests
**4.** Read the README.md file
**5.** Search for code in the project

[dim]Commands: [green]help[/green] | [green]tools[/green] | [green]exit[/green][/dim]
[dim]Type a number (1-5) or describe your request[/dim]
"""
        self.console.print(Panel(quick_start, border_style="cyan", padding=(1, 2)))
    
    def _display_help(self):
        """Display help information"""
        help_text = """
# Help

## Available Commands:
- **help**: Display this help message
- **tools**: List all available tools
- **exit/quit/q**: Exit the assistant

## Example Queries:
- "Show me the content of main.py"
- "Push agent.py to GitHub"
- "Push tool folder to my repo"
- "Run the unit tests"
- "Scan entire project"

## Tips:
- Be specific in your requests
- The assistant can read files, run tests, search the web, and more
- All interactions are saved in checkpoints.db for debugging
        """
        self.console.print(Panel(
            Markdown(help_text),
            title="[bold cyan]Help[/bold cyan]",
            border_style="cyan"
        ))
    
    def _display_tools(self):
        """Display available tools in a tree structure"""
        tree = Tree("[bold cyan]Available Tools[/bold cyan]")
        
        # Group tools by type
        local_branch = tree.add("[yellow]Local Tools[/yellow]")
        github_branch = tree.add("[green]GitHub Tools[/green]")
        mcp_branch = tree.add("[magenta]MCP Tools[/magenta]")
        
        for tool in self.tools:
            if tool in self.mcp_tools:
                mcp_branch.add(f"[magenta]• {tool.name}[/magenta]: {tool.description}")
            elif 'github' in tool.name.lower() or 'repo' in tool.name.lower():
                github_branch.add(f"[green]• {tool.name}[/green]: {tool.description}")
            else:
                local_branch.add(f"[yellow]• {tool.name}[/yellow]: {tool.description}")
        
        self.console.print(tree)