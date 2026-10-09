# BinAssistMCP

> [!IMPORTANT]
> **Planned archival:** BinAssistMCP is planned for archival as Binary Ninja introduces a bundled native MCP server. Native support removes the need to maintain a separate MCP integration and keeps it aligned with Binary Ninja itself. Please use Binary Ninja's native MCP server once it is available in your installed version. This repository will remain available for reference.

> Comprehensive Model Context Protocol (MCP) server for Binary Ninja with AI-powered reverse engineering capabilities

## Summary

BinAssistMCP is a powerful bridge between Binary Ninja and Large Language Models (LLMs) like Claude, providing comprehensive reverse engineering tools through the Model Context Protocol (MCP). It enables AI-assisted binary analysis by exposing Binary Ninja's advanced capabilities through Server-Sent Events (SSE) and Streamable HTTP transports.

### Key Features

- **MCP 2025-11-25 Compliant**: Full support for tool annotations, resources, and prompts
- **Dual Transport Support**: SSE (Server-Sent Events) and Streamable HTTP transports
- **45 Consolidated Tools**: Streamlined Binary Ninja API wrapper with unified tool design
- **8 MCP Resources**: Browsable, cacheable binary metadata
- **7 Guided Prompts**: Pre-built workflows for common reverse engineering tasks
- **Multi-Binary Sessions**: Concurrent analysis of multiple binaries with intelligent context management
- **Context-Rich Code Output**: Function signatures and Binary Ninja comments are embedded in code results
- **Analysis-Safe Queries**: Code retrieval uses already-loaded IL and never forces global reanalysis
- **Session-Independent Discovery**: Direct tool calls discover open Binary Ninja views without requiring a prior listing call
- **Nonblocking Binary Opens**: Large binaries and `.bndb` databases open asynchronously with pollable operation status
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
├── tools.py         # Binary Ninja API wrapper - 45 MCP tools
├── plugin.py        # Binary Ninja plugin integration
├── context.py       # Thread-safe multi-binary session management
├── config.py        # Pydantic configuration with Binary Ninja settings
├── prompts.py       # 7 guided workflow prompts
├── resources.py     # 8 MCP resource definitions
├── cache.py         # Cache primitives (not currently connected to MCP tools)
├── tasks.py         # Task lifecycle support (tool dispatch is not yet implemented)
├── logging.py       # Binary Ninja logging integration
└── utils.py         # Utility functions

__init__.py          # Plugin entry point (root level)
```

---

## Tools (45 Total)

BinAssistMCP provides 45 tools organized into functional categories. Tools include MCP annotations (`readOnlyHint`, `idempotentHint`) to help clients make informed decisions.

### Binary Management
| Tool | Description |
|------|-------------|
| `list_binaries` | Synchronize with Binary Ninja and list all loaded binary files |
| `get_binary_info` | Check analysis status and metadata |
| `get_binary_status` | Poll a queued open by operation ID, path, or name and report analysis progress |
| `open_binary` | Queue a nonblocking binary or `.bndb` open; raw binaries require a destination `bndb_path` |
| `update_analysis_and_wait` | Force analysis update and wait for completion |
| `export_program` | Export the patched binary or Binary Ninja database to disk |

By default, `open_binary` validates its paths, schedules the Binary Ninja open, and immediately returns an `operation_id` with `status="opening"`. Poll `get_binary_status(operation_id)` until it reports `ready` or `failed`. When ready, its `name` field is the final context name to pass to analysis tools. Concurrent requests for the same path reuse the active operation. Set `wait_for_analysis=true` only when legacy blocking behavior is explicitly required.

### Code Analysis (Consolidated)
| Tool | Description |
|------|-------------|
| `get_code` | Read analysis-safe code with signatures and comments; supports `decompile`, `hlil`, `mlil`, `llil`, `disasm`, and `pseudo_c` |
| `get_function_low_level_il` | Get Low-Level IL for a function |
| `get_function_signature` | Generate the native masked byte signature for a function |
| `analyze_function` | Comprehensive function analysis with control flow and complexity metrics |
| `get_basic_blocks` | Get basic block information for control flow analysis |
| `get_function_stack_layout` | Get stack frame layout with variable offsets |

#### `get_code` behavior

`get_code` is a read-only query. It does not clear `analysis_skipped`, request IL generation, or run global analysis. For `format="decompile"`, it returns already-loaded HLIL when available, then falls back through MLIL and LLIL to instruction-aligned disassembly. Use `update_analysis_and_wait` explicitly when fresh analysis is desired.

The `code` string starts with the current function signature and includes function-level and instruction-level Binary Ninja comments. The response contains:

- `function` and `address`: resolved function identity
- `format`: requested format
- `actual_format`: representation actually returned, or `null` when an explicitly requested IL is unavailable
- `fallback_used`: whether `decompile` used a lower-level representation
- `code`: rendered code, signature, and comments
- `note`: present when a fallback explains why and what was returned

### Cross-References (Consolidated)
| Tool | Description |
|------|-------------|
| `xrefs` | Unified cross-references with `direction` set to `to`, `from`, or `both`; optionally includes call relationships |

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
| `types` | **Unified type management** - actions: `create`, `create_enum`, `create_typedef`, `create_class`, `add_member`, `info`, `list` |
| `get_classes` | List all classes and structures |

### Function Discovery
| Tool | Description |
|------|-------------|
| `get_functions` | List all functions with metadata |
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
| `get_strings` | Paginated string extraction |
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

### Patching
| Tool | Description |
|------|-------------|
| `patch_bytes` | Patch raw bytes in the binary at an address |
| `assemble_code` | Assemble instruction text at an address and optionally patch it |

### Navigation & Bookmarks
| Tool | Description |
|------|-------------|
| `get_current_address` | Get current cursor position with context |
| `get_current_function` | Identify function at current address |
| `bookmarks` | **Unified bookmark management** - actions: `list`, `set`, `remove` |

### Task Management (Experimental)
| Tool | Description |
|------|-------------|
| `start_task` | Create a placeholder background task record; `tool_name` dispatch is not implemented yet |
| `get_task_status` | Check status of async operations |
| `list_tasks` | List all pending/running tasks |
| `cancel_task` | Cancel a running task |

These APIs currently exercise task lifecycle management only. They do not execute the named MCP tool in the background.

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

- **Binary Ninja**: Version 5000 or higher
- **Python**: 3.10+ (typically bundled with Binary Ninja and required by the pinned MCP SDK)
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
git clone https://github.com/symgraph/BinAssistMCP.git
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
| `server.port` | `8000` | Server port |
| `server.transport` | `streamablehttp` | Transport: `streamablehttp` or `sse` |
| `binary.max_binaries` | `10` | Maximum concurrent binaries |
| `plugin.auto_startup` | `true` | Auto-start server on file load |

### Environment Variables

```bash
export BINASSISTMCP_SERVER__HOST=localhost
export BINASSISTMCP_SERVER__PORT=8000
export BINASSISTMCP_SERVER__TRANSPORT=streamablehttp
export BINASSISTMCP_BINARY__MAX_BINARIES=10
```

---

## Usage

### Starting the Server

**Via Binary Ninja Menu:**
1. **Tools** → **BinAssistMCP** → **Start Server**
2. Check log panel for: `BinAssistMCP server started on http://localhost:8000`

**Auto-Startup:**
Server starts automatically when Binary Ninja loads a file (configurable).

### Connecting MCP Clients

**Streamable HTTP (Default):**
```
http://localhost:8000/mcp
```

**Server-Sent Events:**
```
http://localhost:8000/sse
```

### Claude Desktop Configuration

Add to your Claude Desktop MCP configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "binassist": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

---

## Integration Examples

Most binary-specific tools require the context name in `filename`. `list_binaries` returns these names, but it is not an initialization requirement: direct filename-based calls refresh the context from Binary Ninja automatically when necessary.

### Basic Function Analysis
```
User: "Analyze the main function and explain what it does"

Claude uses:
1. list_binaries() - obtain the context filename
2. get_functions(filename='sample.bndb') - find main
3. get_code(filename='sample.bndb', function_name_or_address='main', format='decompile')
4. xrefs(filename='sample.bndb', address_or_name='main', direction='from')
5. analyze_function(filename='sample.bndb', function_name_or_address='main')
```

### Vulnerability Research
```
User: "Find buffer overflow vulnerabilities in input handling functions"

Claude uses:
1. search_functions_advanced(filename='sample.bndb', search_term='strcpy', search_in='calls')
2. get_code(filename='sample.bndb', function_name_or_address='handler', format='decompile')
3. variables(filename='sample.bndb', action='list', function_name_or_address='handler')
4. comments(filename='sample.bndb', action='set', address='0x401000', text='Unchecked copy')
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
| Server won't start | Check port 8000 availability, verify dependencies |
| Connection refused | Ensure server is running, check firewall settings |
| A requested IL is unavailable | Run `update_analysis_and_wait`, then retry `get_code` |
| Binary name is rejected | Call `list_binaries` and use the returned context name |

### Performance

- **Memory usage**: Reduce `max_binaries` setting
- **Code retrieval**: `get_code` returns loaded analysis immediately and falls back rather than triggering analysis

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
