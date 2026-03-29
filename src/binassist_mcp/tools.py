"""
Comprehensive tool implementations for BinAssistMCP

This module provides all the Binary Ninja integration tools.
"""

import functools
import re
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from .function_signature_generator import BinaryNinjaFunctionSignatureGenerator
from .logging import log

try:
    import binaryninja as bn
    BINJA_AVAILABLE = True
except ImportError:
    BINJA_AVAILABLE = False
    log.log_warn("Binary Ninja not available")


def handle_exceptions(func):
    """Decorator to handle exceptions in tool methods"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log.log_error(f"Error in {func.__name__}: {str(e)}")
            raise
    return wrapper


def require_binja(func):
    """Decorator to ensure Binary Ninja is available"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not BINJA_AVAILABLE:
            raise RuntimeError("Binary Ninja is not available")
        return func(*args, **kwargs)
    return wrapper


class BinAssistMCPTools:
    """Comprehensive tool handler for Binary Ninja MCP tools"""
    
    def __init__(self, binary_view):
        """Initialize with a Binary Ninja BinaryView
        
        Args:
            binary_view: Binary Ninja BinaryView object
        """
        if not BINJA_AVAILABLE:
            raise RuntimeError("Binary Ninja is not available")
            
        self.bv = binary_view
        if not self.bv:
            raise ValueError("Binary view cannot be None")
            
    def _resolve_symbol(self, address_or_name: str) -> Optional[int]:
        """Resolve a symbol name or address to a numeric address

        Args:
            address_or_name: Either a hex address string or symbol name

        Returns:
            Numeric address if found, None otherwise

        Raises:
            ValueError: If address is outside binary bounds
        """
        address = None

        # Try to parse as hex address
        try:
            if isinstance(address_or_name, str) and address_or_name.startswith("0x"):
                address = int(address_or_name, 16)
            else:
                address = int(address_or_name, 16)
        except ValueError:
            pass

        # Try to parse as decimal address
        if address is None:
            try:
                addr = int(address_or_name)
                if addr >= 0:
                    address = addr
            except ValueError:
                pass

        # Validate address bounds if we parsed a numeric address
        if address is not None:
            if address < self.bv.start or address > self.bv.end:
                raise ValueError(f"Address {hex(address)} is outside binary bounds ({hex(self.bv.start)} - {hex(self.bv.end)})")
            return address

        # Search by function name
        for func in self.bv.functions:
            if func.name == address_or_name:
                return func.start

        # Search by data variable name
        for addr, var in self.bv.data_vars.items():
            if hasattr(var, 'symbol') and var.symbol and var.symbol.name == address_or_name:
                return addr

        # Search by symbol name
        symbol = self.bv.get_symbol_by_raw_name(str(address_or_name))
        if symbol:
            return symbol.address

        return None
        
    def _get_function_by_name_or_address(self, identifier: Union[str, int]):
        """Get a function by name or address"""
        # Handle address-based lookup
        try:
            if isinstance(identifier, str) and identifier.startswith("0x"):
                addr = int(identifier, 16)
            elif isinstance(identifier, (int, str)):
                addr = int(identifier) if isinstance(identifier, str) else identifier
                
            func = self.bv.get_function_at(addr)
            if func:
                return func
        except ValueError:
            pass
            
        # Handle name-based lookup
        for func in self.bv.functions:
            if func.name == identifier:
                return func
                
        # Try case-insensitive match
        for func in self.bv.functions:
            if func.name.lower() == str(identifier).lower():
                return func
                
        # Try symbol lookup
        symbol = self.bv.get_symbol_by_raw_name(str(identifier))
        if symbol and symbol.address:
            func = self.bv.get_function_at(symbol.address)
            if func:
                return func
                
        return None
        
    # Core analysis tools
    @handle_exceptions
    @require_binja
    def rename_symbol(self, address_or_name: str, new_name: str) -> str:
        """Rename a function or data variable
        
        Args:
            address_or_name: Address (hex string) or name of the symbol
            new_name: New name for the symbol
            
        Returns:
            Success message string
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"No function or data variable found with name/address '{address_or_name}'")
            
        # Try to rename function
        func = self.bv.get_function_at(addr)
        if func:
            old_name = func.name
            func.name = new_name
            return f"Successfully renamed function at {hex(addr)} from '{old_name}' to '{new_name}'"
            
        # Try to rename data variable
        if addr in self.bv.data_vars:
            var = self.bv.data_vars[addr]
            old_name = var.symbol.name if var.symbol else 'unnamed'
            
            # Create a symbol at this address with the new name
            self.bv.define_user_symbol(bn.Symbol(bn.SymbolType.DataSymbol, addr, new_name))
            return f"Successfully renamed data variable at {hex(addr)} from '{old_name}' to '{new_name}'"
            
        raise ValueError(f"No function or data variable found at address {hex(addr)}")
        
    @handle_exceptions
    @require_binja
    def decompile_function(self, address_or_name: str) -> str:
        """Decompile a function to high-level representation
        
        Args:
            address_or_name: Function name or address
            
        Returns:
            Decompiled function code
        """
        func = self._get_function_by_name_or_address(address_or_name)
        if not func:
            raise ValueError(f"Function not found: {address_or_name}")
            
        # Ensure analysis is complete
        func.analysis_skipped = False
        self.bv.update_analysis_and_wait()
        
        # Try High Level IL first
        if hasattr(func, 'hlil') and func.hlil:
            return str(func.hlil)
        # Fall back to Medium Level IL
        elif hasattr(func, 'mlil') and func.mlil:
            return str(func.mlil)
        # Last resort: basic function representation
        else:
            return str(func)
            
    @handle_exceptions
    @require_binja
    def get_function_pseudo_c(self, address_or_name: str) -> str:
        """Get pseudo C code for a function
        
        Args:
            address_or_name: Function name or address
            
        Returns:
            Pseudo C code as string
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"No function found with name/address '{address_or_name}'")
            
        func = self.bv.get_function_at(addr)
        if not func:
            raise ValueError(f"No function found at address {hex(addr)}")
            
        lines = []
        settings = bn.DisassemblySettings()
        settings.set_option(bn.DisassemblyOption.ShowAddress, False)
        settings.set_option(bn.DisassemblyOption.WaitForIL, True)
        
        obj = bn.LinearViewObject.language_representation(self.bv, settings)
        cursor_end = bn.LinearViewCursor(obj)
        cursor_end.seek_to_address(func.highest_address)
        
        body = self.bv.get_next_linear_disassembly_lines(cursor_end)
        cursor_end.seek_to_address(func.highest_address)
        header = self.bv.get_previous_linear_disassembly_lines(cursor_end)
        
        for line in header:
            lines.append(f"{str(line)}\n")
        for line in body:
            lines.append(f"{str(line)}\n")
            
        return ''.join(lines)
        
    @handle_exceptions
    @require_binja
    def get_function_high_level_il(self, address_or_name: str) -> str:
        """Get High Level IL for a function
        
        Args:
            address_or_name: Function name or address
            
        Returns:
            HLIL as string
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"No function found with name/address '{address_or_name}'")
            
        func = self.bv.get_function_at(addr)
        if not func:
            raise ValueError(f"No function found at address {hex(addr)}")
            
        hlil = func.hlil
        if not hlil:
            raise ValueError(f"Failed to get HLIL for function at {hex(addr)}")
            
        lines = []
        for instruction in hlil.instructions:
            lines.append(f"{instruction.address:#x}: {instruction}\n")
            
        return ''.join(lines)
        
    @handle_exceptions
    @require_binja
    def get_function_medium_level_il(self, address_or_name: str) -> str:
        """Get Medium Level IL for a function
        
        Args:
            address_or_name: Function name or address
            
        Returns:
            MLIL as string
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"No function found with name/address '{address_or_name}'")
            
        func = self.bv.get_function_at(addr)
        if not func:
            raise ValueError(f"No function found at address {hex(addr)}")
            
        mlil = func.mlil
        if not mlil:
            raise ValueError(f"Failed to get MLIL for function at {hex(addr)}")
            
        lines = []
        for instruction in mlil.instructions:
            lines.append(f"{instruction.address:#x}: {instruction}\n")
            
        return ''.join(lines)
        
    @handle_exceptions
    @require_binja
    def get_disassembly(self, address_or_name: str, length: Optional[int] = None) -> str:
        """Get disassembly for a function or address range
        
        Args:
            address_or_name: Function name or start address
            length: Optional length in bytes for range disassembly
            
        Returns:
            Disassembly as string
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"No symbol found with name/address '{address_or_name}'")
            
        # Range disassembly if length specified
        if length is not None:
            disasm = []
            current_addr = addr
            remaining_length = length
            
            while remaining_length > 0 and current_addr < self.bv.end:
                instr_length = self.bv.get_instruction_length(current_addr)
                if instr_length == 0:
                    instr_length = 1
                    
                tokens = self.bv.get_disassembly(current_addr)
                if tokens:
                    disasm.append(f"{hex(current_addr)}: {tokens}")
                    
                current_addr += instr_length
                remaining_length -= instr_length
                
            if not disasm:
                raise ValueError(f"Failed to disassemble at address {hex(addr)} with length {length}")
            return '\n'.join(disasm)
            
        # Function disassembly
        func = self.bv.get_function_at(addr)
        if not func:
            raise ValueError(f"No function found at address {hex(addr)}")
            
        result_lines = []
        settings = bn.DisassemblySettings()
        settings.set_option(bn.DisassemblyOption.ShowAddress, True)
        
        obj = bn.LinearViewObject.single_function_disassembly(func, settings)
        cursor = bn.LinearViewCursor(obj)
        cursor.seek_to_begin()
        
        while not cursor.after_end:
            lines = self.bv.get_next_linear_disassembly_lines(cursor)
            if not lines:
                break
            for line in lines:
                result_lines.append(str(line))
                
        if not result_lines:
            raise ValueError(f"Failed to disassemble function at {hex(addr)}")
            
        return '\n'.join(result_lines)
        
    def _get_annotated_instruction(self, addr: int, instr_len: int) -> Optional[str]:
        """Get a single instruction with annotations"""
        try:
            # Get raw bytes
            raw_bytes = self.bv.read(addr, instr_len)
            hex_bytes = ' '.join(f'{b:02x}' for b in raw_bytes)
            
            # Get disassembly
            disasm_text = self.bv.get_disassembly(addr)
            if not disasm_text:
                disasm_text = hex_bytes + " ; [Raw bytes]"
                
            # Annotate call instructions
            if "call" in disasm_text.lower():
                addr_pattern = r'0x[0-9a-fA-F]+'
                match = re.search(addr_pattern, disasm_text)
                if match:
                    call_addr_str = match.group(0)
                    call_addr = int(call_addr_str, 16)
                    sym = self.bv.get_symbol_at(call_addr)
                    if sym and hasattr(sym, "name"):
                        disasm_text = disasm_text.replace(call_addr_str, sym.name)
                        
            # Get comment if any (check both function-level and BV-level)
            comment = self._get_comment_at(addr)

            # Format final line
            line = f"0x{addr:08x}  {disasm_text}"
            if comment:
                line += f"  ; {comment}"

            return line
            
        except Exception as e:
            log.log_debug(f"Error annotating instruction at {hex(addr)}: {e}")
            return f"0x{addr:08x}  {hex_bytes} ; [Error: {str(e)}]"
            
    # Information retrieval tools
    @handle_exceptions
    @require_binja
    def get_functions(self) -> List[Dict[str, Any]]:
        """Get list of all functions"""
        functions = []
        for func in self.bv.functions:
            functions.append({
                "name": func.name,
                "address": hex(func.start),
                "size": func.total_bytes,
                "symbol_type": str(func.symbol.type) if func.symbol else None,
                "parameter_count": len(func.parameter_vars),
                "return_type": str(func.return_type) if func.return_type else None,
                "basic_block_count": len(list(func.basic_blocks))
            })
        return functions
        
    @handle_exceptions
    @require_binja
    def search_functions_by_name(self, search_term: str) -> List[Dict[str, Any]]:
        """Search functions by name substring
        
        Args:
            search_term: Substring to search for
            
        Returns:
            List of matching functions
        """
        if not search_term:
            return []
            
        matches = []
        for func in self.bv.functions:
            if search_term.lower() in func.name.lower():
                matches.append({
                    "name": func.name,
                    "address": hex(func.start),
                    "symbol_type": str(func.symbol.type) if func.symbol else None
                })
                
        matches.sort(key=lambda x: x["name"])
        return matches
        
    @handle_exceptions
    @require_binja
    def get_imports(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get imported symbols grouped by module"""
        imports = {}
        
        for sym in self.bv.get_symbols_of_type(bn.SymbolType.ImportedFunctionSymbol):
            module = sym.namespace or 'unknown'
            if module not in imports:
                imports[module] = []
                
            imports[module].append({
                "name": sym.name,
                "address": hex(sym.address),
                "type": str(sym.type),
                "ordinal": getattr(sym, 'ordinal', None)
            })
            
        for sym in self.bv.get_symbols_of_type(bn.SymbolType.ImportedDataSymbol):
            module = sym.namespace or 'unknown'
            if module not in imports:
                imports[module] = []
                
            imports[module].append({
                "name": sym.name,
                "address": hex(sym.address),
                "type": str(sym.type),
                "ordinal": getattr(sym, 'ordinal', None)
            })
            
        return imports
        
    @handle_exceptions
    @require_binja
    def get_exports(self) -> List[Dict[str, Any]]:
        """Get exported symbols"""
        exports = []
        
        for sym in self.bv.get_symbols_of_type(bn.SymbolType.FunctionSymbol):
            if sym.binding == bn.SymbolBinding.GlobalBinding:
                exports.append({
                    "name": sym.name,
                    "address": hex(sym.address),
                    "type": str(sym.type),
                    "ordinal": getattr(sym, 'ordinal', None)
                })
                
        for sym in self.bv.get_symbols_of_type(bn.SymbolType.DataSymbol):
            if sym.binding == bn.SymbolBinding.GlobalBinding:
                exports.append({
                    "name": sym.name,
                    "address": hex(sym.address),
                    "type": str(sym.type),
                    "ordinal": getattr(sym, 'ordinal', None)
                })
                
        return exports
        
    @handle_exceptions
    @require_binja
    def get_strings(self, page_size: int = 100, page_number: int = 1) -> Dict[str, Any]:
        """Get strings found in the binary with pagination

        Args:
            page_size: Number of strings per page (default: 100)
            page_number: Page number starting from 1 (default: 1)

        Returns:
            Dictionary containing:
                - strings: List of string information dictionaries
                - page_size: The page size used
                - page_number: The current page number
                - total_count: Total number of strings
                - total_pages: Total number of pages
        """
        all_strings = []
        for string in self.bv.strings:
            all_strings.append({
                "value": string.value,
                "address": hex(string.start),
                "length": string.length,
                "type": str(string.type)
            })

        # Calculate pagination
        total_count = len(all_strings)
        start_idx = (page_number - 1) * page_size
        end_idx = start_idx + page_size

        # Get the paginated slice
        paginated_strings = all_strings[start_idx:end_idx]

        return {
            "strings": paginated_strings,
            "page_size": page_size,
            "page_number": page_number,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
        }
        
    @handle_exceptions
    @require_binja
    def get_segments(self) -> List[Dict[str, Any]]:
        """Get memory segments"""
        segments = []
        for segment in self.bv.segments:
            segments.append({
                "start": hex(segment.start),
                "end": hex(segment.end),
                "length": segment.length,
                "readable": segment.readable,
                "writable": segment.writable,
                "executable": segment.executable,
                "data_offset": segment.data_offset,
                "data_length": segment.data_length
            })
        return segments
        
    @handle_exceptions
    @require_binja
    def get_sections(self) -> List[Dict[str, Any]]:
        """Get binary sections"""
        sections = []
        for section in self.bv.sections.values():
            sections.append({
                "name": section.name,
                "start": hex(section.start),
                "end": hex(section.end),
                "length": section.length,
                "type": section.type,
                "align": section.align,
                "entry_size": section.entry_size
            })
        return sections
        
    @handle_exceptions
    @require_binja
    def get_triage_summary(self) -> Dict[str, Any]:
        """Get binary triage summary"""
        return {
            "file_metadata": {
                "filename": self.bv.file.filename,
                "file_size": self.bv.length,
                "view_type": self.bv.view_type
            },
            "binary_info": {
                "platform": str(self.bv.platform),
                "architecture": self.bv.arch.name if self.bv.arch else None,
                "entry_point": hex(self.bv.entry_point),
                "base_address": hex(self.bv.start),
                "end_address": hex(self.bv.end),
                "endianness": self.bv.endianness.name,
                "address_size": self.bv.address_size
            },
            "statistics": {
                "function_count": len(list(self.bv.functions)),
                "string_count": len(list(self.bv.strings)),
                "segment_count": len(self.bv.segments),
                "section_count": len(self.bv.sections)
            }
        }
        
    @handle_exceptions
    @require_binja
    def update_analysis_and_wait(self) -> str:
        """Update analysis and wait for completion"""
        self.bv.update_analysis_and_wait()
        return f"Analysis updated successfully for {self.bv.file.filename}"
        
    # Class and namespace management tools
    @handle_exceptions
    @require_binja
    def get_classes(self) -> List[Dict[str, Any]]:
        """Get all classes/structs/types in the binary"""
        classes = []
        
        # Get all user-defined types
        for type_name, type_obj in self.bv.types.items():
            if isinstance(type_obj, bn.StructureType):
                members = []
                for member in type_obj.members:
                    members.append({
                        "name": member.name,
                        "type": str(member.type),
                        "offset": member.offset
                    })
                    
                classes.append({
                    "name": type_name,
                    "type": "struct",  # Binary Ninja uses StructureType for both classes and structs
                    "size": type_obj.width,
                    "members": members,
                    "member_count": len(members)
                })
                
        return classes
        
    @handle_exceptions
    @require_binja
    def create_class(self, name: str, size: int) -> str:
        """Create a new class/struct type
        
        Args:
            name: Name of the class/struct
            size: Size in bytes
            
        Returns:
            Success message
        """
        if name in self.bv.types:
            raise ValueError(f"Type '{name}' already exists")
            
        # Create empty structure
        struct = bn.StructureBuilder.create()
        struct.width = size
        
        # Define the type
        self.bv.define_user_type(name, struct)
        return f"Successfully created class/struct '{name}' with size {size} bytes"
        
    @handle_exceptions
    @require_binja
    def add_class_member(self, class_name: str, member_name: str, member_type: str, offset: int) -> str:
        """Add a member to an existing class/struct
        
        Args:
            class_name: Name of the class/struct
            member_name: Name of the member
            member_type: Type of the member (e.g., 'int32_t', 'char*')
            offset: Offset within the struct
            
        Returns:
            Success message
        """
        if class_name not in self.bv.types:
            raise ValueError(f"Class/struct '{class_name}' not found")
            
        struct_type = self.bv.types[class_name]
        if not isinstance(struct_type, bn.StructureType):
            raise ValueError(f"'{class_name}' is not a class or struct")
            
        # Parse the member type
        try:
            parsed_type = self.bv.parse_type_string(member_type)[0]
        except Exception as e:
            raise ValueError(f"Invalid type '{member_type}': {str(e)}")

        # Create a mutable copy of the existing structure
        struct_builder = struct_type.mutable_copy()

        # Insert the new member at the specified offset
        struct_builder.insert(offset, parsed_type, member_name)

        # Redefine the type in the binary view
        self.bv.define_user_type(class_name, struct_builder)
        return f"Successfully added member '{member_name}' to '{class_name}' at offset {offset}"
        
    @handle_exceptions
    @require_binja
    def get_namespaces(self) -> List[Dict[str, Any]]:
        """Get all namespaces in the binary"""
        namespaces = {}
        
        # Collect all symbols and group by namespace
        for sym in self.bv.symbols.values():
            for symbol in sym:
                ns = symbol.namespace if symbol.namespace else "global"
                if ns not in namespaces:
                    namespaces[ns] = []
                    
                namespaces[ns].append({
                    "name": symbol.name,
                    "address": hex(symbol.address),
                    "type": str(symbol.type)
                })
                
        # Convert to list format
        result = []
        for ns_name, symbols in namespaces.items():
            result.append({
                "namespace": ns_name,
                "symbol_count": len(symbols),
                "symbols": symbols
            })
            
        return result
        
    # Advanced data management tools
    @handle_exceptions
    @require_binja
    def create_data_var(self, address: str, var_type: str, name: Optional[str] = None) -> str:
        """Create a data variable at the specified address
        
        Args:
            address: Address in hex format (e.g., '0x401000')
            var_type: Type of the variable (e.g., 'int32_t', 'char*')
            name: Optional name for the variable
            
        Returns:
            Success message
        """
        addr = self._resolve_symbol(address)
        if addr is None:
            raise ValueError(f"Invalid address: {address}")
            
        # Parse the type
        try:
            parsed_type = self.bv.parse_type_string(var_type)[0]
        except Exception as e:
            raise ValueError(f"Invalid type '{var_type}': {str(e)}")
            
        # Define the data variable
        self.bv.define_user_data_var(addr, parsed_type)
        
        # Set name if provided
        if name:
            symbol = bn.Symbol(bn.SymbolType.DataSymbol, addr, name)
            self.bv.define_user_symbol(symbol)
            
        return f"Successfully created data variable at {hex(addr)} with type '{var_type}'" + (f" named '{name}'" if name else "")
        
    @handle_exceptions
    @require_binja
    def get_data_vars(self) -> List[Dict[str, Any]]:
        """Get all data variables in the binary"""
        data_vars = []
        
        for addr, var in self.bv.data_vars.items():
            var_info = {
                "address": hex(addr),
                "type": str(var.type),
                "size": var.type.width if var.type else 0,
                "name": None
            }
            
            # Try to get symbol name
            symbol = self.bv.get_symbol_at(addr)
            if symbol:
                var_info["name"] = symbol.name
                
            data_vars.append(var_info)
            
        # Sort by address
        data_vars.sort(key=lambda x: int(x["address"], 16))
        return data_vars
        
    @handle_exceptions
    @require_binja
    def get_data_at_address(self, address: str, size: Optional[int] = None) -> Dict[str, Any]:
        """Get data at a specific address
        
        Args:
            address: Address in hex format
            size: Optional size to read (if not specified, uses data var size or default 16)
            
        Returns:
            Dictionary with data information
        """
        addr = self._resolve_symbol(address)
        if addr is None:
            raise ValueError(f"Invalid address: {address}")
            
        # Determine size to read
        read_size = size
        if not read_size:
            # Check if there's a data variable at this address
            if addr in self.bv.data_vars:
                var = self.bv.data_vars[addr]
                read_size = var.type.width if var.type else 16
            else:
                read_size = 16  # Default size
                
        # Read raw data
        try:
            raw_data = self.bv.read(addr, read_size)
        except Exception as e:
            raise ValueError(f"Failed to read data at {hex(addr)}: {str(e)}")
            
        # Get hex representation
        hex_data = ' '.join(f'{b:02x}' for b in raw_data)
        
        result = {
            "address": hex(addr),
            "size": read_size,
            "raw_hex": hex_data,
            "raw_bytes": list(raw_data)
        }
        
        # Try to interpret as different types
        if len(raw_data) >= 4:
            try:
                result["as_uint32"] = int.from_bytes(raw_data[:4], byteorder='little')
                result["as_int32"] = int.from_bytes(raw_data[:4], byteorder='little', signed=True)
            except:
                pass
                
        if len(raw_data) >= 8:
            try:
                result["as_uint64"] = int.from_bytes(raw_data[:8], byteorder='little')
                result["as_int64"] = int.from_bytes(raw_data[:8], byteorder='little', signed=True)
            except:
                pass
                
        # Try to interpret as string
        try:
            # Find null terminator or use all data
            null_pos = raw_data.find(0)
            str_data = raw_data[:null_pos] if null_pos != -1 else raw_data
            result["as_string"] = str_data.decode('utf-8', errors='replace')
        except:
            pass
            
        # Check if there's a defined data variable
        if addr in self.bv.data_vars:
            var = self.bv.data_vars[addr]
            result["defined_type"] = str(var.type)
            symbol = self.bv.get_symbol_at(addr)
            if symbol:
                result["symbol_name"] = symbol.name
                
        return result
        
    # Comment management tools

    def _set_comment_at(self, addr: int, comment: str) -> None:
        """Set a comment at an address, using function-level comments when possible.

        If the address is within a function, uses function.set_comment_at() to set
        a function-level comment. Otherwise, falls back to bv.set_comment_at() for
        BV-level (address) comments.

        Args:
            addr: Address to set comment at
            comment: Comment text (empty string to clear)
        """
        funcs = self.bv.get_functions_containing(addr)
        if funcs:
            funcs[0].set_comment_at(addr, comment)
        else:
            self.bv.set_comment_at(addr, comment)

    def _get_comment_at(self, addr: int) -> Optional[str]:
        """Get a comment at an address, checking both function and BV-level comments.

        First checks for function-level comments if the address is within a function,
        then falls back to BV-level (address) comments.

        Args:
            addr: Address to get comment from

        Returns:
            Comment text or None if no comment exists
        """
        # Check function-level comments first
        funcs = self.bv.get_functions_containing(addr)
        if funcs:
            comment = funcs[0].get_comment_at(addr)
            if comment:
                return comment
        # Fall back to BV-level comments
        return self.bv.get_comment_at(addr) or None

    def _remove_comment_at(self, addr: int) -> bool:
        """Remove a comment at an address from both function and BV levels.

        Args:
            addr: Address to remove comment from

        Returns:
            True if a comment was removed, False if no comment existed
        """
        removed = False
        # Remove function-level comment if exists
        funcs = self.bv.get_functions_containing(addr)
        if funcs:
            if funcs[0].get_comment_at(addr):
                funcs[0].set_comment_at(addr, "")
                removed = True
        # Also remove BV-level comment if exists
        if self.bv.get_comment_at(addr):
            self.bv.set_comment_at(addr, "")
            removed = True
        return removed

    @handle_exceptions
    @require_binja
    def set_comment(self, address: str, comment: str) -> str:
        """Set a comment at the specified address

        Uses function-level comments when the address is within a function,
        which is the preferred approach for Binary Ninja.

        Args:
            address: Address in hex format
            comment: Comment text

        Returns:
            Success message
        """
        addr = self._resolve_symbol(address)
        if addr is None:
            raise ValueError(f"Invalid address: {address}")

        self._set_comment_at(addr, comment)
        return f"Successfully set comment at {hex(addr)}: '{comment}'"
        
    @handle_exceptions
    @require_binja
    def get_comment(self, address: str) -> Optional[str]:
        """Get comment at the specified address

        Checks both function-level and BV-level comments.

        Args:
            address: Address in hex format

        Returns:
            Comment text or None if no comment exists
        """
        addr = self._resolve_symbol(address)
        if addr is None:
            raise ValueError(f"Invalid address: {address}")

        return self._get_comment_at(addr)
        
    @handle_exceptions
    @require_binja
    def get_all_comments(self) -> List[Dict[str, Any]]:
        """Get all comments in the binary

        Collects comments from:
        - Function-level comments (function.comment)
        - Function instruction comments (function.get_comment_at)
        - BV-level address comments (bv.address_comments)
        """
        comments = []
        seen_addresses = set()

        # Get function-level comments and function instruction comments
        for func in self.bv.functions:
            # Function description comment
            if func.comment:
                comments.append({
                    "address": hex(func.start),
                    "type": "function",
                    "comment": func.comment,
                    "function_name": func.name
                })

            # Function instruction comments
            for addr, comment in func.comments.items():
                if comment:
                    comments.append({
                        "address": hex(addr),
                        "type": "instruction",
                        "comment": comment,
                        "function_name": func.name
                    })
                    seen_addresses.add(addr)

        # Get BV-level address comments (that weren't already captured as function comments)
        for addr, comment in self.bv.address_comments.items():
            if addr not in seen_addresses and comment:
                # Try to find containing function for context
                funcs = self.bv.get_functions_containing(addr)
                func_name = funcs[0].name if funcs else None
                comments.append({
                    "address": hex(addr),
                    "type": "address",
                    "comment": comment,
                    "function_name": func_name
                })

        # Sort by address
        comments.sort(key=lambda x: int(x["address"], 16))
        return comments
        
    @handle_exceptions
    @require_binja
    def remove_comment(self, address: str) -> str:
        """Remove comment at the specified address

        Removes comments from both function-level and BV-level storage.

        Args:
            address: Address in hex format

        Returns:
            Success message
        """
        addr = self._resolve_symbol(address)
        if addr is None:
            raise ValueError(f"Invalid address: {address}")

        if not self._remove_comment_at(addr):
            return f"No comment found at {hex(addr)}"

        return f"Successfully removed comment at {hex(addr)}"
        
    @handle_exceptions
    @require_binja
    def set_function_comment(self, function_name_or_address: str, comment: str) -> str:
        """Set a comment for an entire function
        
        Args:
            function_name_or_address: Function name or address
            comment: Comment text
            
        Returns:
            Success message
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        func.comment = comment
        return f"Successfully set comment for function '{func.name}': '{comment}'"
        
    # Variable management tools
    @handle_exceptions
    @require_binja
    def create_variable(self, function_name_or_address: str, var_name: str, var_type: str, storage: str = "auto") -> str:
        """Create a local variable in a function
        
        Args:
            function_name_or_address: Function name or address
            var_name: Variable name
            var_type: Variable type (e.g., 'int32_t', 'char*')
            storage: Storage type ('auto', 'register', etc.)
            
        Returns:
            Success message
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        # Parse the type
        try:
            parsed_type = self.bv.parse_type_string(var_type)[0]
        except Exception as e:
            raise ValueError(f"Invalid type '{var_type}': {str(e)}")
            
        # Create the variable (this is simplified - Binary Ninja's variable management is complex)
        # In practice, you might need to analyze the function's IL to determine proper variable placement
        var = bn.Variable.from_identifier(self.bv.arch, 0, var_name)  # Simplified approach
        
        # Try to set the variable type in the function
        try:
            func.create_user_var(var, parsed_type, var_name)
            return f"Successfully created variable '{var_name}' with type '{var_type}' in function '{func.name}'"
        except Exception as e:
            raise ValueError(f"Failed to create variable: {str(e)}")
            
    @handle_exceptions
    @require_binja
    def get_variables(self, function_name_or_address: str) -> List[Dict[str, Any]]:
        """Get all variables in a function
        
        Args:
            function_name_or_address: Function name or address
            
        Returns:
            List of variables with their information
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        variables = []
        
        # Get parameter variables
        for param in func.parameter_vars:
            variables.append({
                "name": param.name,
                "type": self._get_variable_type_safe(func, param),
                "category": "parameter",
                "storage": str(param.storage),
                "identifier": str(param.identifier)
            })
            
        # Get local variables
        for var in func.vars:
            if var not in func.parameter_vars:
                variables.append({
                    "name": var.name,
                    "type": self._get_variable_type_safe(func, var), 
                    "category": "local",
                    "storage": str(var.storage),
                    "identifier": str(var.identifier)
                })
                
        return variables
        
    @handle_exceptions
    @require_binja
    def rename_variable(self, function_name_or_address: str, old_name: str, new_name: str) -> str:
        """Rename a variable in a function
        
        Args:
            function_name_or_address: Function name or address
            old_name: Current variable name
            new_name: New variable name
            
        Returns:
            Success message
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        # Find the variable
        target_var = None
        for var in func.vars:
            if var.name == old_name:
                target_var = var
                break
                
        if not target_var:
            raise ValueError(f"Variable '{old_name}' not found in function '{func.name}'")
            
        # Rename the variable
        target_var.name = new_name
        return f"Successfully renamed variable from '{old_name}' to '{new_name}' in function '{func.name}'"
        
    @handle_exceptions
    @require_binja
    def set_variable_type(self, function_name_or_address: str, var_name: str, var_type: str) -> str:
        """Set the type of a variable in a function
        
        Args:
            function_name_or_address: Function name or address
            var_name: Variable name
            var_type: New variable type (e.g., 'int32_t', 'char*')
            
        Returns:
            Success message
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        # Find the variable
        target_var = None
        for var in func.vars:
            if var.name == var_name:
                target_var = var
                break
                
        if not target_var:
            raise ValueError(f"Variable '{var_name}' not found in function '{func.name}'")
            
        # Parse the type
        try:
            parsed_type = self.bv.parse_type_string(var_type)[0]
        except Exception as e:
            raise ValueError(f"Invalid type '{var_type}': {str(e)}")
            
        # Set the variable type
        func.create_user_var(target_var, parsed_type, var_name)
        return f"Successfully set type of variable '{var_name}' to '{var_type}' in function '{func.name}'"
        
    # Type system tools
    @handle_exceptions
    @require_binja
    def create_type(self, name: str, definition: str) -> str:
        """Create a new data type from a C-like definition
        
        Args:
            name: Name of the type
            definition: Type definition (e.g., 'struct { int x; int y; }', 'int*')
            
        Returns:
            Success message
        """
        if name in self.bv.types:
            raise ValueError(f"Type '{name}' already exists")
            
        # Parse the type definition
        try:
            parsed_type = self.bv.parse_type_string(definition)[0]
        except Exception as e:
            raise ValueError(f"Invalid type definition '{definition}': {str(e)}")
            
        # Define the type
        self.bv.define_user_type(name, parsed_type)
        return f"Successfully created type '{name}' with definition '{definition}'"
        
    @handle_exceptions
    @require_binja
    def get_types(self, page_size: int = 100, page_number: int = 1) -> Dict[str, Any]:
        """Get all user-defined types with pagination

        Args:
            page_size: Number of types per page (default: 100)
            page_number: Page number starting from 1 (default: 1)

        Returns:
            Dictionary containing:
                - types: List of type information dictionaries
                - page_size: The page size used
                - page_number: The current page number
                - total_count: Total number of types
        """
        all_types = []

        for type_name, type_obj in self.bv.types.items():
            type_info = {
                "name": type_name,
                "size": type_obj.width if hasattr(type_obj, 'width') else None,
                "category": self._get_type_category(type_obj),
                "definition": str(type_obj)
            }

            # Add additional info for complex types
            if isinstance(type_obj, bn.StructureType):
                type_info["member_count"] = len(type_obj.members) if hasattr(type_obj, 'members') else 0
            elif isinstance(type_obj, bn.EnumerationType):
                type_info["member_count"] = len(type_obj.members) if hasattr(type_obj, 'members') else 0
            elif isinstance(type_obj, bn.ArrayType):
                type_info["element_type"] = str(type_obj.element_type)
                type_info["count"] = type_obj.count

            all_types.append(type_info)

        # Calculate pagination
        total_count = len(all_types)
        start_idx = (page_number - 1) * page_size
        end_idx = start_idx + page_size

        # Get the paginated slice
        paginated_types = all_types[start_idx:end_idx]

        return {
            "types": paginated_types,
            "page_size": page_size,
            "page_number": page_number,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
        }
        
    def _get_type_category(self, type_obj) -> str:
        """Get the category of a type object"""
        if isinstance(type_obj, bn.StructureType):
            return "struct"
        elif isinstance(type_obj, bn.EnumerationType):
            return "enum"
        elif isinstance(type_obj, bn.ArrayType):
            return "array"
        elif isinstance(type_obj, bn.PointerType):
            return "pointer"
        elif isinstance(type_obj, bn.FunctionType):
            return "function"
        else:
            return "primitive"
            
    @handle_exceptions
    @require_binja
    def create_enum(self, name: str, members: Dict[str, int]) -> str:
        """Create an enumeration type
        
        Args:
            name: Name of the enum
            members: Dictionary of member names to values
            
        Returns:
            Success message
        """
        if name in self.bv.types:
            raise ValueError(f"Type '{name}' already exists")
            
        # Create enumeration
        enum_builder = bn.EnumerationBuilder.create()
        for member_name, value in members.items():
            enum_builder.append(member_name, value)
            
        # Define the type
        enum_type = bn.Type.enumeration_type(self.bv.arch, enum_builder, 4)  # 4-byte enum
        self.bv.define_user_type(name, enum_type)
        
        member_list = ', '.join(f"{k}={v}" for k, v in members.items())
        return f"Successfully created enum '{name}' with members: {member_list}"
        
    @handle_exceptions
    @require_binja
    def create_typedef(self, name: str, base_type: str) -> str:
        """Create a type alias (typedef)
        
        Args:
            name: Name of the typedef
            base_type: Base type to alias
            
        Returns:
            Success message
        """
        if name in self.bv.types:
            raise ValueError(f"Type '{name}' already exists")
            
        # Parse the base type
        try:
            parsed_type = self.bv.parse_type_string(base_type)[0]
        except Exception as e:
            raise ValueError(f"Invalid base type '{base_type}': {str(e)}")
            
        # Create named type
        named_type = bn.Type.named_type_from_type(name, parsed_type)
        self.bv.define_user_type(name, named_type)
        
        return f"Successfully created typedef '{name}' for type '{base_type}'"
        
    @handle_exceptions
    @require_binja
    def get_type_info(self, type_name: str) -> Dict[str, Any]:
        """Get detailed information about a specific type
        
        Args:
            type_name: Name of the type
            
        Returns:
            Dictionary with type information
        """
        if type_name not in self.bv.types:
            raise ValueError(f"Type '{type_name}' not found")
            
        type_obj = self.bv.types[type_name]
        
        info = {
            "name": type_name,
            "category": self._get_type_category(type_obj),
            "size": type_obj.width if hasattr(type_obj, 'width') else None,
            "definition": str(type_obj)
        }
        
        # Add specific information based on type
        if isinstance(type_obj, bn.StructureType):
            info["members"] = []
            if hasattr(type_obj, 'members'):
                for member in type_obj.members:
                    info["members"].append({
                        "name": member.name,
                        "type": str(member.type),
                        "offset": member.offset,
                        "size": member.type.width if member.type else 0
                    })
                    
        elif isinstance(type_obj, bn.EnumerationType):
            info["members"] = []
            if hasattr(type_obj, 'members'):
                for member in type_obj.members:
                    info["members"].append({
                        "name": member.name,
                        "value": member.value
                    })
                    
        elif isinstance(type_obj, bn.ArrayType):
            info["element_type"] = str(type_obj.element_type)
            info["count"] = type_obj.count
            info["element_size"] = type_obj.element_type.width if type_obj.element_type else 0
            
        elif isinstance(type_obj, bn.PointerType):
            info["target_type"] = str(type_obj.target)
            info["pointer_size"] = type_obj.width
            
        elif isinstance(type_obj, bn.FunctionType):
            info["return_type"] = str(type_obj.return_value)
            info["parameters"] = []
            if hasattr(type_obj, 'parameters'):
                for param in type_obj.parameters:
                    info["parameters"].append({
                        "type": str(param.type),
                        "name": param.name if hasattr(param, 'name') else None
                    })
                    
        return info
        
    # Function analysis tools
    @handle_exceptions
    @require_binja
    def get_call_graph(self, function_name_or_address: Optional[str] = None) -> Dict[str, Any]:
        """Get call graph information for a function or entire binary
        
        Args:
            function_name_or_address: Optional function name or address (if None, returns global call graph)
            
        Returns:
            Call graph information
        """
        if function_name_or_address:
            # Single function call graph
            func = self._get_function_by_name_or_address(function_name_or_address)
            if not func:
                raise ValueError(f"Function not found: {function_name_or_address}")
                
            calls_to = []
            calls_from = []
            
            # Get functions this function calls
            for call_site in func.call_sites:
                try:
                    if hasattr(call_site, 'address'):
                        called_func = self.bv.get_function_at(call_site.address)
                        if called_func:
                            calls_to.append({
                                "function": called_func.name,
                                "address": hex(called_func.start),
                                "call_site": hex(call_site.address)
                            })
                except Exception as e:
                    log.log_debug(f"Error processing call_site in get_call_graph: {e}")
                    continue
                    
            # Get functions that call this function
            for caller in func.callers:
                calls_from.append({
                    "function": caller.name,
                    "address": hex(caller.start)
                })
                
            return {
                "function": func.name,
                "address": hex(func.start),
                "calls_to": calls_to,
                "calls_from": calls_from,
                "call_count_out": len(calls_to),
                "call_count_in": len(calls_from)
            }
        else:
            # Global call graph
            call_graph = {}
            for func in self.bv.functions:
                calls = []
                for call_site in func.call_sites:
                    try:
                        if hasattr(call_site, 'address'):
                            called_func = self.bv.get_function_at(call_site.address)
                            if called_func:
                                calls.append({
                                    "target": called_func.name,
                                    "address": hex(called_func.start)
                                })
                    except Exception as e:
                        log.log_debug(f"Error processing call_site in global call_graph: {e}")
                        continue
                        
                call_graph[func.name] = {
                    "address": hex(func.start),
                    "calls": calls,
                    "call_count": len(calls)
                }
                
            return {"call_graph": call_graph, "function_count": len(call_graph)}
            
    @handle_exceptions
    @require_binja
    def analyze_function(self, function_name_or_address: str) -> Dict[str, Any]:
        """Perform comprehensive analysis of a function
        
        Args:
            function_name_or_address: Function name or address
            
        Returns:
            Comprehensive function analysis
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")
            
        # Basic function info
        analysis = {
            "name": func.name,
            "address": hex(func.start),
            "size": func.total_bytes,
            "basic_block_count": len(list(func.basic_blocks)),
            "instruction_count": sum(len(bb) for bb in func.basic_blocks),
            "parameter_count": len(func.parameter_vars),
            "local_variable_count": len(func.vars) - len(func.parameter_vars),
            "complexity": {
                "cyclomatic": self._calculate_cyclomatic_complexity(func),
                "call_depth": len(list(func.call_sites))
            }
        }
        
        # Control flow analysis
        analysis["control_flow"] = {
            "entry_point": hex(func.start),
            "exit_points": [hex(bb.end) for bb in func.basic_blocks if len(bb.outgoing_edges) == 0],
            "branch_count": sum(1 for bb in func.basic_blocks if len(bb.outgoing_edges) > 1),
            "loop_count": self._count_loops(func)
        }
        
        # Call analysis
        calls_to = []
        for call_site in func.call_sites:
            try:
                if hasattr(call_site, 'address'):
                    called_func = self.bv.get_function_at(call_site.address)
                    if called_func:
                        calls_to.append(called_func.name)
            except Exception as e:
                log.log_debug(f"Error processing call_site in analyze_function: {e}")
                continue
                
        analysis["calls"] = {
            "outgoing": calls_to,
            "incoming": [caller.name for caller in func.callers],
            "external_calls": [call for call in calls_to if call.startswith("sub_") or "@" in call]
        }
        
        # Type information
        analysis["types"] = {
            "return_type": str(func.return_type) if func.return_type else "void",
            "parameters": [
                {
                    "name": param.name,
                    "type": self._get_variable_type_safe(func, param)
                }
                for param in func.parameter_vars
            ]
        }
        
        return analysis

    @handle_exceptions
    @require_binja
    def get_function_signature(self, function_name_or_address: str) -> Dict[str, Any]:
        """Get the native BinAssist byte signature for a function."""
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")

        generator = BinaryNinjaFunctionSignatureGenerator(self.bv)
        return {
            "name": func.name,
            "address": hex(func.start),
            "signature": generator.generate(func),
        }
        
    def _calculate_cyclomatic_complexity(self, func) -> int:
        """Calculate cyclomatic complexity for a function"""
        # Cyclomatic complexity = E - N + 2P
        # Where E = edges, N = nodes, P = connected components (usually 1)
        edges = sum(len(bb.outgoing_edges) for bb in func.basic_blocks)
        nodes = len(list(func.basic_blocks))
        return edges - nodes + 2
        
    def _count_loops(self, func) -> int:
        """Count the number of loops in a function"""
        # Simple heuristic: count back edges
        loop_count = 0
        visited = set()
        
        for bb in func.basic_blocks:
            for edge in bb.outgoing_edges:
                if edge.target.start <= bb.start and edge.target.start not in visited:
                    loop_count += 1
                visited.add(bb.start)
                
        return loop_count
        
    @handle_exceptions
    @require_binja
    def get_cross_references(self, address_or_name: str) -> Dict[str, Any]:
        """Get cross-references for a function or address
        
        Args:
            address_or_name: Function name or address
            
        Returns:
            Cross-reference information
        """
        addr = self._resolve_symbol(address_or_name)
        if addr is None:
            raise ValueError(f"Invalid address or symbol: {address_or_name}")
            
        xrefs_to = []
        xrefs_from = []
        
        # Get references TO this address
        for ref in self.bv.get_code_refs(addr):
            try:
                # Extract address from ReferenceSource object
                if hasattr(ref, 'address'):
                    ref_addr = ref.address
                    ref_func = self.bv.get_function_at(ref_addr)
                    xrefs_to.append({
                        "address": hex(ref_addr),
                        "function": ref_func.name if ref_func else "unknown",
                        "type": "code"
                    })
                else:
                    log.log_debug(f"Code ref has no address attribute: {ref} (type: {type(ref)})")
            except Exception as e:
                log.log_debug(f"Error processing code reference: {e}")
                continue
            
        for ref in self.bv.get_data_refs(addr):
            try:
                # Extract address from ReferenceSource object
                if hasattr(ref, 'address'):
                    ref_addr = ref.address
                    ref_func = self.bv.get_function_at(ref_addr)
                    xrefs_to.append({
                        "address": hex(ref_addr),
                        "function": ref_func.name if ref_func else "unknown", 
                        "type": "data"
                    })
                else:
                    log.log_debug(f"Data ref has no address attribute: {ref} (type: {type(ref)})")
            except Exception as e:
                log.log_debug(f"Error processing data reference: {e}")
                continue
            
        # Get references FROM this address (if it's a function)
        func = self.bv.get_function_at(addr)
        if func:
            try:
                # Method 1: Use call_sites with proper error handling
                for call_site in func.call_sites:
                    try:
                        # Debug info for troubleshooting
                        log.log_debug(f"Processing call_site: type={type(call_site)}, attributes={[attr for attr in dir(call_site) if not attr.startswith('_')]}")
                        
                        # Try to get address from ReferenceSource object
                        if hasattr(call_site, 'address'):
                            call_addr = call_site.address
                            called_func = self.bv.get_function_at(call_addr)
                            xrefs_from.append({
                                "address": hex(call_addr),
                                "target": called_func.name if called_func else "unknown",
                                "type": "call"
                            })
                        else:
                            log.log_debug(f"call_site has no address attribute: {call_site}")
                            
                    except Exception as call_site_error:
                        log.log_debug(f"Error processing individual call_site: {call_site_error}")
                        continue
                        
            except Exception as call_sites_error:
                log.log_debug(f"Error accessing call_sites, trying alternative method: {call_sites_error}")
                
                # Method 2: Alternative using callees if call_sites fails
                try:
                    callees = func.callees
                    call_sites_list = list(func.call_sites) if hasattr(func, 'call_sites') else []
                    
                    for i, callee in enumerate(callees):
                        try:
                            # Try to get corresponding call site address
                            if i < len(call_sites_list) and hasattr(call_sites_list[i], 'address'):
                                call_addr = call_sites_list[i].address
                                xrefs_from.append({
                                    "address": hex(call_addr),
                                    "target": callee.name,
                                    "type": "call"
                                })
                            else:
                                # Fallback: just list the callee without specific call site
                                xrefs_from.append({
                                    "address": "unknown",
                                    "target": callee.name,
                                    "type": "call"
                                })
                        except Exception as callee_error:
                            log.log_debug(f"Error processing callee {i}: {callee_error}")
                            continue
                            
                except Exception as callees_error:
                    log.log_debug(f"Both call_sites and callees methods failed: {callees_error}")
                
        return {
            "address": hex(addr),
            "symbol_name": address_or_name if not address_or_name.startswith("0x") else None,
            "references_to": xrefs_to,
            "references_from": xrefs_from,
            "total_refs_to": len(xrefs_to),
            "total_refs_from": len(xrefs_from)
        }
        
    # Enhanced function listing tools
    @handle_exceptions
    @require_binja
    def get_functions_advanced(self, 
                               name_filter: Optional[str] = None,
                               min_size: Optional[int] = None,
                               max_size: Optional[int] = None,
                               has_parameters: Optional[bool] = None,
                               sort_by: str = "address",
                               limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get functions with advanced filtering and search capabilities
        
        Args:
            name_filter: Filter by function name (substring match)
            min_size: Minimum function size in bytes
            max_size: Maximum function size in bytes
            has_parameters: Filter by whether function has parameters
            sort_by: Sort by 'address', 'name', 'size', or 'complexity'
            limit: Maximum number of results
            
        Returns:
            Filtered and sorted list of functions
        """
        functions = []
        
        for func in self.bv.functions:
            # Apply filters
            if name_filter and name_filter.lower() not in func.name.lower():
                continue
                
            if min_size is not None and func.total_bytes < min_size:
                continue
                
            if max_size is not None and func.total_bytes > max_size:
                continue
                
            if has_parameters is not None:
                func_has_params = len(func.parameter_vars) > 0
                if has_parameters != func_has_params:
                    continue
                    
            func_info = {
                "name": func.name,
                "address": hex(func.start),
                "size": func.total_bytes,
                "parameter_count": len(func.parameter_vars),
                "basic_block_count": len(list(func.basic_blocks)),
                "complexity": self._calculate_cyclomatic_complexity(func),
                "call_count": len(list(func.call_sites)),
                "caller_count": len(list(func.callers)),
                "return_type": str(func.return_type) if func.return_type else "void"
            }
            
            functions.append(func_info)
            
        # Sort functions
        if sort_by == "name":
            functions.sort(key=lambda x: x["name"].lower())
        elif sort_by == "size":
            functions.sort(key=lambda x: x["size"], reverse=True)
        elif sort_by == "complexity":
            functions.sort(key=lambda x: x["complexity"], reverse=True)
        else:  # default to address
            functions.sort(key=lambda x: int(x["address"], 16))
            
        # Apply limit
        if limit is not None:
            functions = functions[:limit]
            
        return functions
        
    @handle_exceptions
    @require_binja
    def search_functions_advanced(self, 
                                  search_term: str,
                                  search_in: str = "name",
                                  case_sensitive: bool = False) -> List[Dict[str, Any]]:
        """Advanced function search with multiple search targets
        
        Args:
            search_term: Term to search for
            search_in: Where to search ('name', 'comment', 'calls', 'variables')
            case_sensitive: Whether search should be case sensitive
            
        Returns:
            List of matching functions
        """
        if not search_term:
            return []
            
        matches = []
        search_lower = search_term.lower() if not case_sensitive else search_term
        
        for func in self.bv.functions:
            match_found = False
            match_reason = []
            
            if search_in in ["name", "all"]:
                func_name = func.name if case_sensitive else func.name.lower()
                if search_lower in func_name:
                    match_found = True
                    match_reason.append("name")
                    
            if search_in in ["comment", "all"]:
                if func.comment:
                    comment = func.comment if case_sensitive else func.comment.lower()
                    if search_lower in comment:
                        match_found = True
                        match_reason.append("comment")
                        
            if search_in in ["calls", "all"]:
                for call_site in func.call_sites:
                    try:
                        if hasattr(call_site, 'address'):
                            called_func = self.bv.get_function_at(call_site.address)
                            if called_func:
                                called_name = called_func.name if case_sensitive else called_func.name.lower()
                                if search_lower in called_name:
                                    match_found = True
                                    match_reason.append("calls")
                                    break
                    except Exception as e:
                        log.log_debug(f"Error processing call_site in search_functions_advanced: {e}")
                        continue
                            
            if search_in in ["variables", "all"]:
                for var in func.vars:
                    var_name = var.name if case_sensitive else var.name.lower()
                    if search_lower in var_name:
                        match_found = True
                        match_reason.append("variables")
                        break
                        
            if match_found:
                matches.append({
                    "name": func.name,
                    "address": hex(func.start),
                    "size": func.total_bytes,
                    "match_reason": match_reason,
                    "comment": func.comment if func.comment else None
                })
                
        # Sort by relevance (name matches first, then others)
        matches.sort(key=lambda x: (
            0 if "name" in x["match_reason"] else 1,
            x["name"].lower()
        ))
        
        return matches
        
    @handle_exceptions
    @require_binja
    def get_function_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about all functions in the binary"""
        if not self.bv.functions:
            return {"error": "No functions found in binary"}
            
        sizes = [func.total_bytes for func in self.bv.functions]
        complexities = [self._calculate_cyclomatic_complexity(func) for func in self.bv.functions]
        param_counts = [len(func.parameter_vars) for func in self.bv.functions]
        bb_counts = [len(list(func.basic_blocks)) for func in self.bv.functions]
        
        return {
            "total_functions": len(list(self.bv.functions)),
            "size_statistics": {
                "min": min(sizes),
                "max": max(sizes),
                "average": sum(sizes) / len(sizes),
                "total": sum(sizes)
            },
            "complexity_statistics": {
                "min": min(complexities),
                "max": max(complexities),
                "average": sum(complexities) / len(complexities)
            },
            "parameter_statistics": {
                "min": min(param_counts),
                "max": max(param_counts),
                "average": sum(param_counts) / len(param_counts),
                "functions_with_params": sum(1 for count in param_counts if count > 0)
            },
            "basic_block_statistics": {
                "min": min(bb_counts),
                "max": max(bb_counts),
                "average": sum(bb_counts) / len(bb_counts),
                "total": sum(bb_counts)
            },
            "top_largest_functions": [
                {"name": func.name, "address": hex(func.start), "size": func.total_bytes}
                for func in sorted(self.bv.functions, key=lambda f: f.total_bytes, reverse=True)[:10]
            ],
            "top_most_complex_functions": [
                {"name": func.name, "address": hex(func.start), "complexity": self._calculate_cyclomatic_complexity(func)}
                for func in sorted(self.bv.functions, key=lambda f: self._calculate_cyclomatic_complexity(f), reverse=True)[:10]
            ]
        }
        
    @handle_exceptions
    @require_binja
    def get_current_address(self) -> Dict[str, Any]:
        """Get the current address/offset in the binary view
        
        Returns:
            Dictionary containing current address information
        """
        if not hasattr(self.bv, 'offset'):
            # Fallback: try to get the entry point or first function
            if self.bv.entry_points:
                current_addr = self.bv.entry_points[0]
            elif self.bv.functions:
                current_addr = next(iter(self.bv.functions)).start
            else:
                current_addr = self.bv.start
            
            return {
                "address": hex(current_addr),
                "decimal": current_addr,
                "note": "No current offset available, showing entry point or start address",
                "has_current_offset": False
            }
        
        current_addr = self.bv.offset
        
        # Get additional context about this address
        result = {
            "address": hex(current_addr),
            "decimal": current_addr,
            "has_current_offset": True
        }
        
        # Check if address is in a function
        functions = self.bv.get_functions_containing(current_addr)
        if functions:
            func = functions[0]  # Take the first function if multiple
            result["in_function"] = {
                "name": func.name,
                "start": hex(func.start),
                "end": hex(func.start + func.total_bytes),
                "offset_in_function": current_addr - func.start
            }
        else:
            result["in_function"] = None
            
        # Check if address has a symbol
        symbol = self.bv.get_symbol_at(current_addr)
        if symbol:
            result["symbol"] = {
                "name": symbol.name,
                "type": str(symbol.type)
            }
        else:
            result["symbol"] = None
            
        # Check if it's in a segment
        for segment in self.bv.segments:
            if segment.start <= current_addr < segment.end:
                result["segment"] = {
                    "start": hex(segment.start),
                    "end": hex(segment.end),
                    "readable": segment.readable,
                    "writable": segment.writable,
                    "executable": segment.executable
                }
                break
        else:
            result["segment"] = None
            
        # Try to get disassembly at current address
        try:
            disasm = self.bv.get_disassembly(current_addr)
            if disasm:
                result["disassembly"] = disasm
        except:
            result["disassembly"] = None
            
        return result
        
    @handle_exceptions
    @require_binja
    def get_current_function(self) -> Dict[str, Any]:
        """Get the current function (function containing the current address)
        
        Returns:
            Dictionary containing current function name and address
        """
        if not hasattr(self.bv, 'offset'):
            return {
                "error": "No current offset available",
                "has_current_offset": False
            }
            
        current_addr = self.bv.offset
        
        # Get functions containing the current address
        functions = self.bv.get_functions_containing(current_addr)
        
        if not functions:
            return {
                "current_address": hex(current_addr),
                "function": None,
                "message": "Current address is not within any function"
            }
            
        # If multiple functions contain this address, take the first one
        func = functions[0]
        
        result = {
            "current_address": hex(current_addr),
            "function": {
                "name": func.name,
                "address": hex(func.start)
            }
        }
        
        # If there are multiple functions at this address, note them
        if len(functions) > 1:
            result["note"] = f"Multiple functions at this address ({len(functions)} total)"
            
        return result
        
    def _get_variable_type_safe(self, func, var) -> str:
        """Safely get variable type with fallbacks for API compatibility

        Args:
            func: Function object
            var: Variable object

        Returns:
            String representation of variable type or 'unknown'
        """
        try:
            if hasattr(func, 'get_variable_type'):
                var_type = func.get_variable_type(var)
                return str(var_type) if var_type else "unknown"
            elif hasattr(var, 'type') and var.type:
                return str(var.type)
            else:
                return "unknown"
        except Exception as e:
            log.log_debug(f"Failed to get variable type: {e}")
            return "unknown"

    # ==================== CONSOLIDATED TOOLS ====================
    # These unified tools reduce tool count while maintaining functionality

    @handle_exceptions
    @require_binja
    def get_code(self, function_name_or_address: str, format: str = "decompile") -> Dict[str, Any]:
        """Get function code in specified format.

        Unified tool consolidating: decompile_function, get_function_pseudo_c,
        get_function_high_level_il, get_function_medium_level_il, get_disassembly,
        get_function_low_level_il

        Args:
            function_name_or_address: Function identifier (name or hex address)
            format: Output format - one of:
                - 'decompile': High-level decompiled code (default)
                - 'hlil': High Level Intermediate Language
                - 'mlil': Medium Level Intermediate Language
                - 'llil': Low Level Intermediate Language
                - 'disasm': Assembly disassembly
                - 'pseudo_c': Pseudo C code

        Returns:
            Dictionary with function info and code in requested format
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")

        result = {
            "function": func.name,
            "address": hex(func.start),
            "format": format,
            "code": None
        }

        if format == "decompile":
            # Use existing decompile logic
            func.analysis_skipped = False
            self.bv.update_analysis_and_wait()
            if hasattr(func, 'hlil') and func.hlil:
                result["code"] = str(func.hlil)
            elif hasattr(func, 'mlil') and func.mlil:
                result["code"] = str(func.mlil)
            else:
                lines = []
                for block in func.basic_blocks:
                    for i in range(block.start, block.end):
                        disasm = self.bv.get_disassembly(i)
                        if disasm:
                            lines.append(f"{hex(i)}: {disasm}")
                result["code"] = "\n".join(lines)

        elif format == "hlil":
            func.analysis_skipped = False
            self.bv.update_analysis_and_wait()
            if hasattr(func, 'hlil') and func.hlil:
                lines = []
                for block in func.hlil:
                    for instr in block:
                        lines.append(str(instr))
                result["code"] = "\n".join(lines)
            else:
                result["code"] = "HLIL not available for this function"

        elif format == "mlil":
            func.analysis_skipped = False
            self.bv.update_analysis_and_wait()
            if hasattr(func, 'mlil') and func.mlil:
                lines = []
                for block in func.mlil:
                    for instr in block:
                        lines.append(str(instr))
                result["code"] = "\n".join(lines)
            else:
                result["code"] = "MLIL not available for this function"

        elif format == "llil":
            func.analysis_skipped = False
            self.bv.update_analysis_and_wait()
            if hasattr(func, 'llil') and func.llil:
                lines = []
                for block in func.llil:
                    for instr in block:
                        lines.append(f"{hex(instr.address)}: {instr}")
                result["code"] = "\n".join(lines)
            else:
                result["code"] = "LLIL not available for this function"

        elif format == "disasm":
            lines = []
            for block in func.basic_blocks:
                for i in range(block.start, block.end):
                    disasm = self.bv.get_disassembly(i)
                    if disasm:
                        lines.append(f"{hex(i)}: {disasm}")
            result["code"] = "\n".join(lines)

        elif format == "pseudo_c":
            func.analysis_skipped = False
            self.bv.update_analysis_and_wait()
            if hasattr(func, 'hlil') and func.hlil:
                # Build pseudo-C representation
                code_lines = []
                params = ", ".join([
                    f"{self._get_variable_type_safe(func, p)} {p.name}"
                    for p in func.parameter_vars
                ]) if func.parameter_vars else "void"
                return_type = str(func.return_type) if func.return_type else "void"
                code_lines.append(f"{return_type} {func.name}({params}) {{")
                for block in func.hlil:
                    for instr in block:
                        code_lines.append(f"    {instr}")
                code_lines.append("}")
                result["code"] = "\n".join(code_lines)
            else:
                result["code"] = "Pseudo-C not available (HLIL unavailable)"

        else:
            raise ValueError(f"Unknown format: {format}. Valid: decompile, hlil, mlil, llil, disasm, pseudo_c")

        return result

    @handle_exceptions
    @require_binja
    def comments(self, action: str, address: str = "", text: str = "",
                 function_name_or_address: str = "") -> Union[str, Dict, List, None]:
        """Unified comment management tool.

        Consolidates: set_comment, get_comment, get_all_comments, remove_comment, set_function_comment

        Args:
            action: Action to perform - one of:
                - 'get': Get comment at address
                - 'set': Set comment at address (requires address and text)
                - 'list': List all comments in binary
                - 'remove': Remove comment at address
                - 'set_function': Set function comment (requires function_name_or_address and text)
            address: Address in hex format (for get/set/remove actions)
            text: Comment text (for set/set_function actions)
            function_name_or_address: Function identifier (for set_function action)

        Returns:
            Varies by action - string for set/remove, dict/list for get/list
        """
        if action == "get":
            if not address:
                raise ValueError("Address required for 'get' action")
            addr = self._resolve_symbol(address)
            if addr is None:
                raise ValueError(f"Invalid address: {address}")
            return self._get_comment_at(addr)

        elif action == "set":
            if not address or not text:
                raise ValueError("Address and text required for 'set' action")
            addr = self._resolve_symbol(address)
            if addr is None:
                raise ValueError(f"Invalid address: {address}")
            self._set_comment_at(addr, text)
            return f"Comment set at {hex(addr)}"

        elif action == "list":
            return self.get_all_comments()

        elif action == "remove":
            if not address:
                raise ValueError("Address required for 'remove' action")
            addr = self._resolve_symbol(address)
            if addr is None:
                raise ValueError(f"Invalid address: {address}")
            if not self._remove_comment_at(addr):
                return f"No comment found at {hex(addr)}"
            return f"Comment removed at {hex(addr)}"

        elif action == "set_function":
            if not function_name_or_address or not text:
                raise ValueError("function_name_or_address and text required for 'set_function' action")
            func = self._get_function_by_name_or_address(function_name_or_address)
            if not func:
                raise ValueError(f"Function not found: {function_name_or_address}")
            func.comment = text
            return f"Function comment set for '{func.name}'"

        else:
            raise ValueError(f"Unknown action: {action}. Valid: get, set, list, remove, set_function")

    @handle_exceptions
    @require_binja
    def variables_unified(self, action: str, function_name_or_address: str,
                         var_name: str = "", var_type: str = "",
                         new_name: str = "", storage: str = "auto") -> Union[str, List]:
        """Unified variable management tool.

        Consolidates: create_variable, get_variables, rename_variable, set_variable_type

        Args:
            action: Action to perform - one of:
                - 'list': List all variables in function
                - 'create': Create new variable (requires var_name, var_type)
                - 'rename': Rename variable (requires var_name, new_name)
                - 'set_type': Set variable type (requires var_name, var_type)
            function_name_or_address: Function identifier
            var_name: Variable name (for create/rename/set_type)
            var_type: Variable type (for create/set_type)
            new_name: New variable name (for rename)
            storage: Storage type for create ('auto', 'register', etc.)

        Returns:
            List for 'list' action, success message string for others
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")

        if action == "list":
            return self.get_variables(function_name_or_address)

        elif action == "create":
            if not var_name or not var_type:
                raise ValueError("var_name and var_type required for 'create' action")
            return self.create_variable(function_name_or_address, var_name, var_type, storage)

        elif action == "rename":
            if not var_name or not new_name:
                raise ValueError("var_name and new_name required for 'rename' action")
            return self.rename_variable(function_name_or_address, var_name, new_name)

        elif action == "set_type":
            if not var_name or not var_type:
                raise ValueError("var_name and var_type required for 'set_type' action")
            return self.set_variable_type(function_name_or_address, var_name, var_type)

        else:
            raise ValueError(f"Unknown action: {action}. Valid: list, create, rename, set_type")

    @handle_exceptions
    @require_binja
    def types_unified(self, action: str, name: str = "", kind: str = "",
                     definition: str = "", size: int = 0, members: Dict = None,
                     base_type: str = "", class_name: str = "",
                     member_name: str = "", member_type: str = "", offset: int = 0) -> Union[str, List, Dict]:
        """Unified type management tool.

        Consolidates: create_type, create_class, create_enum, create_typedef, add_class_member, get_types, get_type_info

        Args:
            action: Action to perform - one of:
                - 'list': List all types (paginated)
                - 'info': Get info about specific type (requires name)
                - 'create': Create type from definition (requires name, definition)
                - 'create_class': Create class/struct (requires name, size)
                - 'create_enum': Create enum (requires name, members dict)
                - 'create_typedef': Create typedef (requires name, base_type)
                - 'add_member': Add member to class (requires class_name, member_name, member_type, offset)
            name: Type/class/enum name
            kind: Type kind (for future extension)
            definition: C-like type definition
            size: Size in bytes (for create_class)
            members: Dictionary of enum members {name: value}
            base_type: Base type for typedef
            class_name: Class name for add_member
            member_name: Member name
            member_type: Member type
            offset: Member offset in struct

        Returns:
            Varies by action
        """
        if action == "list":
            return self.get_types()

        elif action == "info":
            if not name:
                raise ValueError("name required for 'info' action")
            return self.get_type_info(name)

        elif action == "create":
            if not name or not definition:
                raise ValueError("name and definition required for 'create' action")
            return self.create_type(name, definition)

        elif action == "create_class":
            if not name or size <= 0:
                raise ValueError("name and size required for 'create_class' action")
            return self.create_class(name, size)

        elif action == "create_enum":
            if not name or not members:
                raise ValueError("name and members required for 'create_enum' action")
            return self.create_enum(name, members)

        elif action == "create_typedef":
            if not name or not base_type:
                raise ValueError("name and base_type required for 'create_typedef' action")
            return self.create_typedef(name, base_type)

        elif action == "add_member":
            if not class_name or not member_name or not member_type:
                raise ValueError("class_name, member_name, and member_type required for 'add_member' action")
            return self.add_class_member(class_name, member_name, member_type, offset)

        else:
            raise ValueError(f"Unknown action: {action}. Valid: list, info, create, create_class, create_enum, create_typedef, add_member")

    @handle_exceptions
    @require_binja
    def xrefs(self, address_or_name: str, direction: str = "both",
              include_calls: bool = True) -> Dict[str, Any]:
        """Unified cross-reference tool.

        Consolidates: get_call_graph, get_cross_references

        Args:
            address_or_name: Address or symbol name to analyze
            direction: Reference direction - 'to', 'from', or 'both'
            include_calls: Include function call relationships

        Returns:
            Dictionary with cross-reference information
        """
        result = {
            "target": address_or_name,
            "direction": direction,
            "references_to": [],
            "references_from": [],
            "call_graph": None
        }

        addr = self._resolve_symbol(address_or_name)
        func = self._get_function_by_name_or_address(address_or_name)

        if addr is None and func is None:
            raise ValueError(f"Could not resolve: {address_or_name}")

        if addr:
            # Get references TO this address
            if direction in ("to", "both"):
                refs = self.bv.get_code_refs(addr)
                for ref in refs:
                    ref_func = self.bv.get_function_at(ref.address)
                    result["references_to"].append({
                        "address": hex(ref.address),
                        "function": ref_func.name if ref_func else None
                    })

            # Get references FROM this address (if it's a function)
            if direction in ("from", "both") and func:
                for block in func.basic_blocks:
                    for i in range(block.start, block.end):
                        for ref in self.bv.get_code_refs_from(i):
                            target_func = self.bv.get_function_at(ref)
                            result["references_from"].append({
                                "from_address": hex(i),
                                "to_address": hex(ref),
                                "to_function": target_func.name if target_func else None
                            })

        # Include call graph if requested
        if include_calls and func:
            result["call_graph"] = {
                "function": func.name,
                "address": hex(func.start),
                "callers": [],
                "callees": []
            }

            # Get callers
            for ref in self.bv.get_code_refs(func.start):
                caller_func = self.bv.get_function_at(ref.address)
                if caller_func and caller_func != func:
                    result["call_graph"]["callers"].append({
                        "name": caller_func.name,
                        "address": hex(caller_func.start)
                    })

            # Get callees
            if hasattr(func, 'callees'):
                for callee in func.callees:
                    result["call_graph"]["callees"].append({
                        "name": callee.name,
                        "address": hex(callee.start)
                    })

        return result

    @handle_exceptions
    @require_binja
    def get_function_low_level_il(self, address_or_name: str) -> str:
        """Get Low Level IL for a function.

        Args:
            address_or_name: Function name or address

        Returns:
            LLIL as string
        """
        func = self._get_function_by_name_or_address(address_or_name)
        if not func:
            raise ValueError(f"Function not found: {address_or_name}")

        func.analysis_skipped = False
        self.bv.update_analysis_and_wait()

        if hasattr(func, 'llil') and func.llil:
            lines = []
            for block in func.llil:
                for instr in block:
                    lines.append(f"{hex(instr.address)}: {instr}")
            return "\n".join(lines)

        return "LLIL not available for this function"

    @handle_exceptions
    @require_binja
    def search_strings(self, pattern: str, case_sensitive: bool = False,
                       page_size: int = 100, page_number: int = 1) -> Dict[str, Any]:
        """Search for strings matching a pattern with pagination.

        Args:
            pattern: Search pattern (substring match)
            case_sensitive: Whether to perform case-sensitive matching
            page_size: Number of results per page (default: 100)
            page_number: Page number starting from 1 (default: 1)

        Returns:
            Dictionary containing:
                - strings: List of matching strings with address, value, and length
                - page_size: The page size used
                - page_number: The current page number
                - total_count: Total number of matching strings
                - total_pages: Total number of pages
        """
        results = []
        search_pattern = pattern if case_sensitive else pattern.lower()

        for string in self.bv.strings:
            string_value = string.value
            compare_value = string_value if case_sensitive else string_value.lower()

            if search_pattern in compare_value:
                results.append({
                    "address": hex(string.start),
                    "value": string_value,
                    "length": string.length,
                    "type": str(string.type)
                })

        # Calculate pagination
        total_count = len(results)
        start_idx = (page_number - 1) * page_size
        end_idx = start_idx + page_size

        # Get the paginated slice
        paginated_results = results[start_idx:end_idx]

        return {
            "strings": paginated_results,
            "page_size": page_size,
            "page_number": page_number,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
        }

    @handle_exceptions
    @require_binja
    def search_bytes(self, pattern: str, start_address: str = "", max_results: int = 100) -> List[Dict[str, Any]]:
        """Search for byte patterns in the binary.

        Args:
            pattern: Hex pattern to search (e.g., '90 90 90' or '909090')
            start_address: Optional start address for search
            max_results: Maximum number of results to return

        Returns:
            List of matches with address and context
        """
        # Clean and parse the pattern
        clean_pattern = pattern.replace(" ", "").replace("0x", "")
        try:
            search_bytes = bytes.fromhex(clean_pattern)
        except ValueError:
            raise ValueError(f"Invalid hex pattern: {pattern}")

        start = self.bv.start
        if start_address:
            resolved = self._resolve_symbol(start_address)
            if resolved:
                start = resolved

        results = []
        current_addr = start

        while len(results) < max_results:
            found = self.bv.find_next_data(current_addr, search_bytes)
            if found is None:
                break

            # Get context around the match
            context_data = self.bv.read(found, min(16, len(search_bytes) + 8))
            context_hex = context_data.hex() if context_data else ""

            # Check if in a function
            funcs = self.bv.get_functions_containing(found)
            func_name = funcs[0].name if funcs else None

            results.append({
                "address": hex(found),
                "context_hex": context_hex,
                "function": func_name
            })

            current_addr = found + 1

        return results

    @handle_exceptions
    @require_binja
    def get_basic_blocks(self, function_name_or_address: str) -> List[Dict[str, Any]]:
        """Get basic blocks for a function (control flow graph).

        Args:
            function_name_or_address: Function identifier

        Returns:
            List of basic blocks with addresses, instructions, and successors
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")

        blocks = []
        for block in func.basic_blocks:
            block_info = {
                "start": hex(block.start),
                "end": hex(block.end),
                "length": block.length,
                "instruction_count": block.instruction_count,
                "successors": [],
                "predecessors": []
            }

            # Get successors
            for edge in block.outgoing_edges:
                if edge.target:
                    block_info["successors"].append({
                        "address": hex(edge.target.start),
                        "type": str(edge.type)
                    })

            # Get predecessors
            for edge in block.incoming_edges:
                if edge.source:
                    block_info["predecessors"].append({
                        "address": hex(edge.source.start)
                    })

            blocks.append(block_info)

        return blocks

    @handle_exceptions
    @require_binja
    def get_function_stack_layout(self, function_name_or_address: str) -> Dict[str, Any]:
        """Get stack frame layout for a function.

        Args:
            function_name_or_address: Function identifier

        Returns:
            Dictionary with stack layout information
        """
        func = self._get_function_by_name_or_address(function_name_or_address)
        if not func:
            raise ValueError(f"Function not found: {function_name_or_address}")

        result = {
            "function": func.name,
            "address": hex(func.start),
            "stack_variables": [],
            "total_local_size": 0
        }

        # Get stack variables
        for var in func.stack_layout:
            var_info = {
                "name": var.name,
                "offset": var.storage,
                "type": self._get_variable_type_safe(func, var)
            }
            result["stack_variables"].append(var_info)

        # Calculate total local size if available
        if hasattr(func, 'stack_adjustment'):
            adj = func.stack_adjustment
            result["total_local_size"] = int(adj) if adj is not None else 0

        return result

    @handle_exceptions
    @require_binja
    def batch_rename(self, renames: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Batch rename multiple symbols.

        Args:
            renames: List of rename operations, each with 'address_or_name' and 'new_name'

        Returns:
            List of results for each rename operation
        """
        results = []

        for rename in renames:
            address_or_name = rename.get("address_or_name", "")
            new_name = rename.get("new_name", "")

            if not address_or_name or not new_name:
                results.append({
                    "address_or_name": address_or_name,
                    "success": False,
                    "error": "Missing address_or_name or new_name"
                })
                continue

            try:
                message = self.rename_symbol(address_or_name, new_name)
                results.append({
                    "address_or_name": address_or_name,
                    "new_name": new_name,
                    "success": True,
                    "message": message
                })
            except Exception as e:
                results.append({
                    "address_or_name": address_or_name,
                    "success": False,
                    "error": str(e)
                })

        return results
