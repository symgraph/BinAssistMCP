# BinAssistMCP

> Comprehensive Model Context Protocol (MCP) server for Binary Ninja with AI-powered reverse engineering capabilities

## Summary

BinAssistMCP is a powerful bridge between Binary Ninja and Large Language Models (LLMs) like Claude, providing comprehensive reverse engineering tools through the Model Context Protocol (MCP). It enables AI-assisted binary analysis by exposing Binary Ninja's advanced capabilities through Server-Sent Events (SSE) and Streamable HTTP transports.

### Key Features

- **MCP 2025-11-25 Compliant**: Full support for tool annotations, resources, and prompts
- **Dual Transport Support**: SSE (Server-Sent Events) and Streamable HTTP transports
- **40 Consolidated Tools**: Streamlined Binary Ninja API wrapper with unified tool design
- **8 MCP Resources**: Browsable, cacheable binary metadata
- **7 Guided Prompts**: Pre-built workflows for common reverse engineering tasks
- **Multi-Binary Sessions**: Concurrent analysis of multiple binaries with intelligent context management
- **Analysis Caching**: LRU cache with binary-scoped invalidation for improved performance
- **Async Task Support**: Non-blocking execution for long-running operations
- **Thread-Safe**: RLock-based synchronization for concurrent access
- **Auto-Integration**: Seamless Binary Ninja plugin with automatic startup capabilities

### Use Cases

- **AI-Assisted Reverse Engineering**: Leverage LLMs for intelligent code analysis and documentation
- **Protocol Analysis**: Trace network data flows and reconstruct protocol structures
- **Vulnerability Research**: Systematic security audits with guided workflows
- **Automated Binary Analysis**: Script complex analysis workflows with natural language
- **Code Understanding**: Generate comprehensive documentation and explanations

---

## Architecture

```
src/binassist_mcp/
├── server.py        # FastMCP server - SSE/Streamable HTTP transport, tool registration
├── tools.py         # Binary Ninja API wrapper - 40 MCP tools
├── plugin.py        # Binary Ninja plugin integration
├── context.py       # Thread-safe multi-binary session management
├── config.py        # Pydantic configuration with Binary Ninja settings
├── prompts.py       # 7 guided workflow prompts
├── resources.py     # 8 MCP resource definitions
├── cache.py         # LRU analysis cache with invalidation
├── tasks.py         # Async task manager for long-running operations
├── logging.py       # Binary Ninja logging integration
└── utils.py         # Utility functions

__init__.py          # Plugin entry point (root level)
```

---

## Tools (40 Total)

BinAssistMCP provides 40 tools organized into functional categories. Tools include MCP annotations (`readOnlyHint`, `idempotentHint`) to help clients make informed decisions.

### Binary Management
| Tool | Description |
|------|-------------|
| `list_binaries` | List all loaded binary files |
| `get_binary_info` | Check analysis status and metadata |
| `update_analysis_and_wait` | Force analysis update and wait for completion |

### Code Analysis (Consolidated)
| Tool | Description |
|------|-------------|
| `get_code` | **Unified code retrieval** - supports formats: `decompile`, `hlil`, `mlil`, `llil`, `disasm`, `pseudo_c` |
| `get_function_low_level_il` | Get Low-Level IL for a function |
| `analyze_function` | Comprehensive function analysis with control flow and complexity metrics |
| `get_basic_blocks` | Get basic block information for control flow analysis |
| `get_function_stack_layout` | Get stack frame layout with variable offsets |

### Cross-References (Consolidated)
| Tool | Description |
|------|-------------|
| `xrefs` | **Unified cross-references** - actions: `refs_to`, `refs_from`, `call_graph` |

### Comments (Consolidated)
| Tool | Description |
|------|-------------|
| `comments` | **Unified comment management** - actions: `get`, `set`, `list`, `remove`, `set_function` |

### Variables (Consolidated)
| Tool | Description |
|------|-------------|
| `variables` | **Unified variable management** - actions: `list`, `create`, `rename`, `set_type`; `rename` supports local/global via `scope` |

### Types (Consolidated)
| Tool | Description |
|------|-------------|
| `types` | **Unified type management** - actions: `create`, `create_enum`, `create_typedef`, `create_class`, `add_member`, `get_info`, `list` |
| `get_classes` | List all classes and structures |

### Function Discovery
| Tool | Description |
|------|-------------|
| `get_functions` | List all functions with metadata (paginated) |
| `get_parent_function` | Get the function containing a given address |
| `search_functions_by_name` | Find functions by name pattern |
| `get_functions_advanced` | Advanced filtering by size, complexity, parameters |
| `search_functions_advanced` | Multi-target search (name, comments, calls, variables) |
| `get_function_statistics` | Comprehensive statistics for all functions |

### Symbol Management
| Tool | Description |
|------|-------------|
| `rename_symbol` | Rename functions and data variables |
| `batch_rename` | Rename multiple symbols in one operation |
| `get_namespaces` | List namespaces and symbol organization |

### Binary Information
| Tool | Description |
|------|-------------|
| `get_imports` | Import table grouped by module |
| `get_exports` | Export table with symbol information |
| `get_strings` | String extraction with filtering |
| `search_strings` | Search strings by pattern |
| `get_segments` | Memory segment layout |
| `get_sections` | Binary section information |
| `get_entry_points` | List all binary entry points |

### Data Analysis
| Tool | Description |
|------|-------------|
| `create_data_var` | Define data variables at addresses |
| `get_data_vars` | List all defined data variables |
| `get_data_at` | Read and analyze raw data |
| `search_bytes` | Search for byte patterns in binary |

### Navigation & Bookmarks
| Tool | Description |
|------|-------------|
| `get_current_address` | Get current cursor position with context |
| `get_current_function` | Identify function at current address |
| `bookmarks` | **Unified bookmark management** - actions: `list`, `set`, `remove` |

### Task Management
| Tool | Description |
|------|-------------|
| `start_task` | Start an async background task |
| `get_task_status` | Check status of async operations |
| `list_tasks` | List all pending/running tasks |
| `cancel_task` | Cancel a running task |

---

## MCP Resources (8 Total)

Resources provide browsable, cacheable data that clients can access without tool calls.

| URI Pattern | Description |
|-------------|-------------|
| `binassist://{filename}/triage_summary` | Complete binary overview |
| `binassist://{filename}/functions` | All functions with metadata |
| `binassist://{filename}/imports` | Import table |
| `binassist://{filename}/exports` | Export table |
| `binassist://{filename}/strings` | String table |
| `binja://{filename}/info` | Binary metadata (arch, platform, entry point) |
| `binja://{filename}/segments` | Memory segments with permissions |
| `binja://{filename}/sections` | Binary sections |

---

## MCP Prompts (7 Total)

Pre-built prompts guide LLMs through structured analysis workflows.

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `analyze_function` | `function_name`, `filename` | Comprehensive function analysis workflow |
| `identify_vulnerability` | `function_name`, `filename` | Security audit checklist (memory safety, input validation, crypto) |
| `document_function` | `function_name`, `filename` | Generate Doxygen-style documentation |
| `trace_data_flow` | `address`, `filename` | Track data dependencies and taint propagation |
| `compare_functions` | `func1`, `func2`, `filename` | Diff two functions for similarity analysis |
| `reverse_engineer_struct` | `address`, `filename` | Recover structure definitions from usage patterns |
| `trace_network_data` | `filename` | Trace POSIX/Winsock send/recv for protocol analysis |

### Example: Network Protocol Analysis

The `trace_network_data` prompt guides analysis of network communication:

1. **Identify Network Functions**: Finds POSIX (`send`/`recv`/`sendto`/`recvfrom`) and Winsock (`WSASend`/`WSARecv`) calls
2. **Trace Call Stacks**: Maps application handlers down to network I/O
3. **Analyze Buffers**: Identifies protocol structures (headers, length fields, TLV encoding)
4. **Reconstruct Protocols**: Generates C struct definitions for message formats
5. **Security Assessment**: Checks for buffer overflows, integer issues, information disclosure

---

## Installation

### Prerequisites

- **Binary Ninja**: Version 4000 or higher
- **Python**: 3.8+ (typically bundled with Binary Ninja)
- **Platform**: Windows, macOS, or Linux

NOTE: Windows users should start with: [BinAssistMCP on Windows](binassistmcp-on-windows.md)

### Option 1: Binary Ninja Plugin Manager (Recommended)

1. Open Binary Ninja
2. Navigate to **Tools** → **Manage Plugins**
3. Search for "BinAssistMCP"
4. Click **Install**
5. Restart Binary Ninja

### Option 2: Manual Installation

```bash
# Clone the repository
git clone https://github.com/jtang613/BinAssistMCP.git
cd BinAssistMCP

# Install dependencies
pip install -r requirements.txt
```

Copy to your Binary Ninja plugins directory:

| Platform | Path |
|----------|------|
| Windows | `%APPDATA%\Binary Ninja\plugins\` |
| macOS | `~/Library/Application Support/Binary Ninja/plugins/` |
| Linux | `~/.binaryninja/plugins/` |

---

## Configuration

### Binary Ninja Settings

Open **Edit** → **Preferences** → **binassistmcp**:

| Setting | Default | Description |
|---------|---------|-------------|
| `server.host` | `localhost` | Server bind address |
| `server.port` | `9090` | Server port |
| `server.transport` | `streamablehttp` | Transport: `streamablehttp` or `sse` |
| `binary.max_binaries` | `10` | Maximum concurrent binaries |
| `plugin.auto_startup` | `true` | Auto-start server on file load |

### Environment Variables

```bash
export BINASSISTMCP_SERVER__HOST=localhost
export BINASSISTMCP_SERVER__PORT=9090
export BINASSISTMCP_SERVER__TRANSPORT=streamablehttp
export BINASSISTMCP_BINARY__MAX_BINARIES=10
```

---

## Usage

### Starting the Server

**Via Binary Ninja Menu:**
1. **Tools** → **BinAssistMCP** → **Start Server**
2. Check log panel for: `BinAssistMCP server started on http://localhost:9090`

**Auto-Startup:**
Server starts automatically when Binary Ninja loads a file (configurable).

### Connecting MCP Clients

**Streamable HTTP (Default):**
```
http://localhost:9090/mcp
```

**Server-Sent Events:**
```
http://localhost:9090/sse
```

### Claude Desktop Configuration

Add to your Claude Desktop MCP configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "binassist": {
      "url": "http://localhost:9090/mcp"
    }
  }
}
```

---

## Integration Examples

### Basic Function Analysis
```
User: "Analyze the main function and explain what it does"

Claude uses:
1. get_functions() - find main
2. get_code(format='decompile') - get readable code
3. xrefs(action='refs_from') - find called functions
4. analyze_function() - get complexity metrics
```

### Vulnerability Research
```
User: "Find buffer overflow vulnerabilities in input handling functions"

Claude uses:
1. search_functions_advanced(search_in='calls') - find memcpy/strcpy callers
2. get_code(format='decompile') - examine implementations
3. variables(action='list') - check buffer sizes
4. comments(action='set') - document findings
```

### Protocol Reverse Engineering
```
User: "Analyze the network protocol used by this binary"

Claude uses the trace_network_data prompt:
1. Identifies send/recv call sites
2. Traces data flow from handlers to network I/O
3. Reconstructs message structures
4. Checks for network vulnerabilities
```

---

## Troubleshooting

### Server Issues

| Problem | Solution |
|---------|----------|
| Server won't start | Check port 9090 availability, verify dependencies |
| Connection refused | Ensure server is running, check firewall settings |
| Tools return errors | Wait for analysis completion, verify binary is loaded |

### Performance

- **Slow decompilation**: Results are cached; second request is faster
- **Memory usage**: Reduce `max_binaries` setting
- **Long operations**: Check task status with `get_task_status`

### Logs

Check Binary Ninja's Log panel for detailed error messages.

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Follow existing code patterns (Pydantic models, type hints, docstrings)
4. Test with multiple binary types
5. Submit a pull request

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
