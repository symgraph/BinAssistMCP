"""
MCP Prompts for BinAssistMCP

This module provides pre-built prompts for common reverse engineering workflows,
guiding the LLM through structured analysis tasks.
"""

from typing import Dict, List, Optional


def analyze_function_prompt(function_name: str, filename: str) -> str:
    """Generate a comprehensive function analysis prompt.

    Args:
        function_name: Name or address of the function to analyze
        filename: Name of the binary file

    Returns:
        Structured analysis prompt
    """
    return f"""Analyze the function '{function_name}' in binary '{filename}' using these steps:

1. **Get Function Code**: Call `get_code` with format='decompile' to get the high-level code
2. **Examine Variables**: Call `get_variables` to identify local variables and parameters
3. **Find Cross-References**: Call `xrefs` to understand callers and callees
4. **Check Comments**: Call `comments` with action='get' to see existing annotations

After gathering this information, provide a summary that includes:
- **Purpose**: What does this function do?
- **Inputs**: What parameters does it take and what are their types?
- **Outputs**: What does it return?
- **Side Effects**: Does it modify global state, call system functions, or access memory?
- **Notable Patterns**: Any interesting code patterns, potential vulnerabilities, or optimizations?

Please be thorough but concise in your analysis."""


def identify_vulnerability_prompt(function_name: str, filename: str) -> str:
    """Generate a security audit prompt for a function.

    Args:
        function_name: Name or address of the function to audit
        filename: Name of the binary file

    Returns:
        Security audit prompt
    """
    return f"""Perform a security audit of function '{function_name}' in binary '{filename}'.

## Analysis Steps

1. **Get Function Code**: Call `get_code` with format='decompile' for high-level view
2. **Get Disassembly**: Call `get_code` with format='disasm' for low-level details
3. **Examine Variables**: Call `get_variables` to identify buffer sizes and types
4. **Find Called Functions**: Call `xrefs` to see what dangerous functions are called

## Security Checklist

Check for the following vulnerability classes:

### Memory Safety
- [ ] Buffer overflows (stack/heap)
- [ ] Use-after-free
- [ ] Double-free
- [ ] Integer overflow/underflow
- [ ] Out-of-bounds access

### Input Validation
- [ ] Format string vulnerabilities
- [ ] Command injection
- [ ] Path traversal
- [ ] Unvalidated user input

### Authentication/Authorization
- [ ] Hardcoded credentials
- [ ] Missing authentication checks
- [ ] Privilege escalation paths

### Cryptography
- [ ] Weak algorithms
- [ ] Improper key management
- [ ] Missing integrity checks

## Output Format

For each potential vulnerability found:
1. Describe the issue
2. Show the relevant code/address
3. Assess the severity (Critical/High/Medium/Low)
4. Suggest remediation"""


def document_function_prompt(function_name: str, filename: str) -> str:
    """Generate a documentation prompt for a function.

    Args:
        function_name: Name or address of the function
        filename: Name of the binary file

    Returns:
        Documentation generation prompt
    """
    return f"""Generate documentation for function '{function_name}' in binary '{filename}'.

## Gather Information

1. **Get Code**: Call `get_code` with format='pseudo_c' for C-style representation
2. **Get Variables**: Call `get_variables` to understand parameter and local types
3. **Get Callers**: Call `xrefs` with direction='to' to find what calls this function
4. **Get Callees**: Call `xrefs` with direction='from' to find what this function calls

## Generate Documentation

Create documentation in the following format:

```c
/**
 * @brief [Brief one-line description]
 *
 * [Detailed description of what the function does, including any
 * important implementation details or algorithms used]
 *
 * @param param1 [Description of first parameter]
 * @param param2 [Description of second parameter]
 * ...
 *
 * @return [Description of return value]
 *
 * @note [Any important notes, side effects, or thread safety considerations]
 *
 * @see [Related functions]
 */
```

Also suggest a meaningful name for the function if it currently has an auto-generated name."""


def trace_data_flow_prompt(address: str, filename: str) -> str:
    """Generate a data flow tracing prompt.

    Args:
        address: Starting address for data flow analysis
        filename: Name of the binary file

    Returns:
        Data flow tracing prompt
    """
    return f"""Trace the data flow starting from address {address} in binary '{filename}'.

## Analysis Steps

1. **Get Context**: Call `get_disassembly` for the instruction at this address
2. **Find Function**: Determine which function contains this address
3. **Get Function Code**: Call `get_code` for the containing function
4. **Trace References**: Call `xrefs` to find related addresses

## Data Flow Analysis

Track the following for the data at/from this address:

### Sources
- Where does this data come from?
- Is it user input, file data, network data, or computed?
- What validations or transformations are applied?

### Sinks
- Where does this data go?
- Is it used in sensitive operations (memory ops, system calls)?
- Could tainted data reach dangerous sinks?

### Transformations
- What operations are performed on the data?
- Are there any sanitization or validation routines?
- Could any transformation introduce vulnerabilities?

## Output

Create a data flow diagram showing:
1. Data sources (entry points)
2. Transformations (operations applied)
3. Data sinks (where data ends up)
4. Potential taint propagation paths"""


def compare_functions_prompt(func1: str, func2: str, filename: str) -> str:
    """Generate a function comparison prompt.

    Args:
        func1: First function name/address
        func2: Second function name/address
        filename: Name of the binary file

    Returns:
        Function comparison prompt
    """
    return f"""Compare functions '{func1}' and '{func2}' in binary '{filename}'.

## Gather Information

For each function, gather:
1. **Decompiled Code**: Call `get_code` with format='decompile'
2. **Variables**: Call `get_variables`
3. **Basic Blocks**: Call `get_basic_blocks` for structure analysis
4. **Cross-References**: Call `xrefs`

## Comparison Analysis

Compare the functions on:

### Structural Similarity
- Number of basic blocks
- Control flow patterns
- Loop structures
- Conditional branches

### Semantic Similarity
- Parameter types and counts
- Return types
- Local variable usage
- Called functions

### Code Differences
- Highlight specific differences in logic
- Note any added/removed functionality
- Identify potential patches or variations

## Output Format

Provide:
1. **Similarity Score**: Estimate how similar the functions are (0-100%)
2. **Key Differences**: List major differences with code snippets
3. **Key Similarities**: List shared patterns or behaviors
4. **Assessment**: Are these likely related? (duplicate, patched version, different implementation of same algorithm?)"""


def reverse_engineer_struct_prompt(address: str, filename: str) -> str:
    """Generate a structure reverse engineering prompt.

    Args:
        address: Address where structure is used
        filename: Name of the binary file

    Returns:
        Structure recovery prompt
    """
    return f"""Reverse engineer the data structure used at address {address} in binary '{filename}'.

## Analysis Steps

1. **Get Context**: Call `get_data_at` to see the raw data
2. **Find Usage**: Call `xrefs` to find all references to this address
3. **Get Functions**: For each function that references it, call `get_code`
4. **Analyze Access Patterns**: Look at how the data is accessed

## Structure Recovery

Analyze the access patterns to determine:

### Field Layout
- What offsets are accessed?
- What are the sizes at each offset?
- What operations are performed (read/write)?

### Field Types
- Infer types from operations (arithmetic, string ops, pointer derefs)
- Identify pointers vs integers vs floating point
- Look for array patterns

### Relationships
- Are there pointers to other structures?
- Is this part of a linked list or tree?
- Are there vtables or function pointers?

## Output Format

Provide a C structure definition:

```c
typedef struct {{
    type1 field1;    // offset 0x00 - description
    type2 field2;    // offset 0x04 - description
    ...
}} StructureName;
```

Also suggest calling `create_class` to define this type in Binary Ninja."""


def trace_network_data_prompt(filename: str) -> str:
    """Generate a network data tracing prompt for protocol analysis.

    Args:
        filename: Name of the binary file

    Returns:
        Network data tracing prompt
    """
    return f"""Trace network send/recv call stacks in binary '{filename}' to analyze protocol data structures and identify network vulnerabilities.

## Phase 1: Identify Network Functions

Search for both POSIX and Winsock network I/O functions:

### POSIX Socket API
Use `search_functions_advanced` with search_in='calls' to find functions calling:
- `send`, `recv` - Basic TCP send/receive
- `sendto`, `recvfrom` - UDP send/receive with address
- `sendmsg`, `recvmsg` - Scatter/gather I/O
- `read`, `write` - When used on socket file descriptors
- `sendfile` - Zero-copy file transfer

### Winsock API
Search for functions calling:
- `send`, `recv` - Basic Winsock send/receive
- `WSASend`, `WSARecv` - Overlapped I/O versions
- `WSASendTo`, `WSARecvFrom` - Datagram versions
- `WSASendMsg`, `WSARecvMsg` - Extended message versions
- `TransmitFile`, `TransmitPackets` - High-performance transfer

## Phase 2: Trace Call Stacks

For each network function found:

1. **Get Callers**: Call `xrefs` with direction='to' to find all callers
2. **Build Call Graph**: Recursively trace callers up to application-level handlers
3. **Get Callees**: Call `xrefs` with direction='from' to find data preparation functions

Create a call hierarchy showing:
```
Application Handler
  └─> Protocol Parser/Builder
       └─> Serialization Layer
            └─> Network I/O (send/recv)
```

## Phase 3: Analyze Buffer Structures

For each send/recv wrapper function:

1. **Get Code**: Call `get_code` with format='decompile' to see high-level logic
2. **Get Variables**: Call `get_variables` to identify buffer parameters
3. **Trace Buffer Origins**: Follow buffer pointers back to their allocation/population

### Payload Structure Analysis
Look for patterns indicating:
- **Fixed headers**: Constant offsets, magic bytes, version fields
- **Length fields**: Size prefixes (often at offset 0-4)
- **Type/opcode fields**: Message type discriminators
- **Checksums/CRCs**: Integrity validation
- **Sequence numbers**: For ordering/replay protection
- **Padding/alignment**: Structure alignment patterns

### Common Protocol Patterns
- TLV (Type-Length-Value) encoding
- Fixed-size message headers with variable payloads
- Delimiter-based framing (newlines, null terminators)
- Chunked transfer encoding

## Phase 4: Reconstruct Protocol Structures

Based on analysis, define C structures:

```c
typedef struct {{
    uint32_t magic;         // offset 0x00 - Protocol magic number
    uint16_t version;       // offset 0x04 - Protocol version
    uint16_t msg_type;      // offset 0x06 - Message type/opcode
    uint32_t payload_len;   // offset 0x08 - Payload length
    uint32_t sequence;      // offset 0x0C - Sequence number
    uint8_t  payload[];     // offset 0x10 - Variable payload
}} NetworkMessage;
```

## Phase 5: Network Vulnerability Assessment

### Buffer Handling
- [ ] Are receive buffer sizes validated against length fields?
- [ ] Is there bounds checking before copying payload data?
- [ ] Could a malformed length field cause buffer overflow?

### Integer Issues
- [ ] Can length fields overflow when used in calculations?
- [ ] Are size comparisons signed vs unsigned consistent?
- [ ] Could multiplication overflow in allocation size?

### State Machine Issues
- [ ] Is message ordering validated (sequence numbers)?
- [ ] Can messages be replayed?
- [ ] Are incomplete messages handled safely?

### Memory Safety
- [ ] Are dynamically allocated buffers freed after use?
- [ ] Could partial reads leave buffers in inconsistent state?
- [ ] Is there proper error handling on network failures?

### Information Disclosure
- [ ] Are uninitialized buffer regions sent over the network?
- [ ] Could error responses leak internal state?
- [ ] Are sensitive fields cleared after use?

## Output

Provide:
1. **Network Function Map**: All identified send/recv call sites with addresses
2. **Call Stack Traces**: From application handlers down to network I/O
3. **Protocol Structure Definitions**: C structs for identified message formats
4. **Data Flow Diagram**: How data moves from application to network
5. **Vulnerability Report**: Any security issues found with severity ratings

Use `comments` to annotate discovered protocol structures and `types` to create Binary Ninja type definitions."""


# Registry of available prompts
PROMPTS = {
    "analyze_function": {
        "name": "Analyze Function",
        "description": "Comprehensive function analysis workflow",
        "arguments": ["function_name", "filename"],
        "generator": analyze_function_prompt
    },
    "identify_vulnerability": {
        "name": "Identify Vulnerability",
        "description": "Security audit checklist for a function",
        "arguments": ["function_name", "filename"],
        "generator": identify_vulnerability_prompt
    },
    "document_function": {
        "name": "Document Function",
        "description": "Generate documentation for a function",
        "arguments": ["function_name", "filename"],
        "generator": document_function_prompt
    },
    "trace_data_flow": {
        "name": "Trace Data Flow",
        "description": "Track data dependencies from an address",
        "arguments": ["address", "filename"],
        "generator": trace_data_flow_prompt
    },
    "compare_functions": {
        "name": "Compare Functions",
        "description": "Diff two functions for similarity/differences",
        "arguments": ["func1", "func2", "filename"],
        "generator": compare_functions_prompt
    },
    "reverse_engineer_struct": {
        "name": "Reverse Engineer Structure",
        "description": "Recover structure definition from usage patterns",
        "arguments": ["address", "filename"],
        "generator": reverse_engineer_struct_prompt
    },
    "trace_network_data": {
        "name": "Trace Network Data",
        "description": "Trace network send/recv call stacks to analyze protocol structures and find vulnerabilities",
        "arguments": ["filename"],
        "generator": trace_network_data_prompt
    }
}


def get_prompt(prompt_name: str, **kwargs) -> Optional[str]:
    """Get a prompt by name with arguments.

    Args:
        prompt_name: Name of the prompt
        **kwargs: Arguments for the prompt

    Returns:
        Generated prompt string or None if not found
    """
    if prompt_name not in PROMPTS:
        return None

    prompt_info = PROMPTS[prompt_name]
    generator = prompt_info["generator"]

    # Check required arguments
    required_args = prompt_info["arguments"]
    for arg in required_args:
        if arg not in kwargs:
            raise ValueError(f"Missing required argument: {arg}")

    return generator(**{k: v for k, v in kwargs.items() if k in required_args})


def list_prompts() -> List[Dict]:
    """List all available prompts.

    Returns:
        List of prompt information dictionaries
    """
    return [
        {
            "name": prompt_name,
            "title": info["name"],
            "description": info["description"],
            "arguments": info["arguments"]
        }
        for prompt_name, info in PROMPTS.items()
    ]
